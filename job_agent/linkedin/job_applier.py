from abc import ABC, abstractmethod
import logging
from selenium.webdriver.remote.webdriver import WebDriver
from .main import JobScraperAgent
import json
import os
import time
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from .model import Country, JobType, ScraperInput, Site, JobResponse, ExperienceLevel
# ... other imports
from selenium.webdriver.support.ui import Select  # <-- THIS LINE MUST BE PRESENT
import PyPDF2

def _extract_pdf_text(pdf_path: str) -> str:
    """Extracts text content from a PDF file."""
    if not os.path.exists(pdf_path):
        logging.error(f"Resume file not found at: {pdf_path}")
        return ""
    
    try:
        logging.info(f"Extracting text from {pdf_path}...")
        text = ""
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text += page.extract_text() or ""
        
        logging.info(f"Successfully extracted {len(text)} characters from resume.")
        return text
    except Exception as e:
        logging.error(f"Failed to read PDF: {e}")
        return ""
    
class BaseApplicator(ABC):
    def __init__(self, driver: WebDriver):
        self.driver = driver
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def apply(self, job_url: str, candidate_data: dict) -> bool:
        """
        Main entry point.
        1. Analyze the page (or load from cache)
        2. Generate answers
        3. Fill and Submit
        """
        pass


class LLMGenericApplicator(BaseApplicator):
    def __init__(self, driver, openai_client, cache_file="site_structures.json"):
        super().__init__(driver)
        self.client = openai_client
        self.cache_file = cache_file
        self.structure_cache = self._load_cache()
        self.wait = WebDriverWait(self.driver, 10)

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                self.logger.warning(f"Cache file {self.cache_file} is corrupted. Starting fresh.")
                return {}
        return {}

    def _save_cache(self):
        with open(self.cache_file, 'w') as f:
            json.dump(self.structure_cache, f, indent=2)

    def _get_domain_key(self, url):
        """
        Returns a key like 'jobs.lever.co' or 'boards.greenhouse.io'.
        We cache based on this, assuming structure is similar across the domain.
        """
        return urlparse(url).netloc

    def _clean_html(self, html_content):
        """
        Crucial: Remove JS, CSS, and SVGs to save tokens and confuse the LLM less.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        for element in soup(['script', 'style', 'svg', 'noscript', 'header', 'footer', 'nav']):
            element.decompose()
        # Return only the form or body
        form = soup.find('form')
        
        # If no form tag, find the main content area
        if not form:
            form = soup.find('main')
        
        return str(form) if form else str(soup.body)

    def _analyze_page_structure(self, url, html_content):
        """
        Phase 1: Ask LLM to find input fields, selectors, options, AND required status.
        """
        domain = self._get_domain_key(url)
        
        # Check cache first
        if domain in self.structure_cache:
            self.logger.info(f"Using cached structure for {domain}")
            return self.structure_cache[domain]

        self.logger.info(f"Analyzing new site structure: {domain}...")
        
        # --- *** UPDATED PROMPT: Added 'required' logic *** ---
        system_prompt = """
        You are a Selenium automation expert. Analyze the provided HTML form.
        Identify all input fields for a job application AND the final submit button.
        
        Return a JSON object with two keys: "fields" and "submit_button".
        
        1. "fields": A list of JSON objects, where each object has:
           - "label": The human readable label (e.g., "First Name").
           - "selector": A precise CSS selector (prefer ID, Name, or data-attributes).
           - "type": One of ["text", "email", "file", "textarea", "select", "radio", "checkbox"].
           - "required": Boolean (true or false). 
        
        2. "submit_button": A single JSON object with:
           - "text": The visible text on the button.
           - "selector": A precise CSS selector.
        
        *** RULES FOR DETERMINING "required" ***
        - Set "required": true if the input has the HTML attribute 'required'.
        - Set "required": true if the input has 'aria-required="true"'.
        - Set "required": true if the Label text contains an asterisk (*).
        - Otherwise, set "required": false.
        
        *** CRITICAL RULES FOR OPTIONS ***
        - If "type" is "select" or "radio", you MUST extract the available options into an "options" list.
        - "options" MUST be a list of {"text": "Visible Text", "value": "html_value_attribute"}.
        
        Example for "fields":
        {
            "label": "First Name", 
            "selector": "#first_name", 
            "type": "text",
            "required": true
        }
        """
        
        # We truncate HTML to avoid token limits
        clean_html = self._clean_html(html_content)[:15000] 

        response = self.client.chat.completions.create(
            model="gpt-4o-mini", 
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Analyze this HTML:\n{clean_html}"}
            ]
        )
        
        structure = json.loads(response.choices[0].message.content)
        
        # Save to cache
        self.structure_cache[domain] = structure
        self._save_cache()
        return structure

    def _generate_field_values(self, form_structure, candidate_data):
        """
        Phase 2: Ask LLM to map Candidate Data -> Form Fields and choose options.
        """
        self.logger.info("Generating answers for form fields...")
        
        # Define the template as a regular string with a placeholder
        prompt_template = """
        You are a job application assistant. 
        Map the candidate's profile to the form fields provided.
        
        Candidate Profile:
        {profile_json}
        
        Rules:
        1. Return JSON: {{ "field_label": "value_to_fill" }}
        2. If a field is marked "required": true in the form structure, you MUST generate a valid answer based on the resume, even if imperfect.
        3. If a field is NOT required and the data is missing from the profile, return "N/A".
        4.  For "select" or "radio" fields, I will provide the "options". 
            You MUST choose the best option and return its corresponding "value" attribute.
            Example: If options are `[...{{"text": "Black or African American", "value": "B"}}]` 
            and profile says `Ethiopian`, you return "B".
        5.  For "select" fields, if no option matches, return the "value" of the first option 
            (it's often "Select..." or empty).
        6.  For file uploads ("Resume/CV"), return the exact "resume_path" from the profile.
        7.  For "Why do you want to work here?", "Salary Expectation", or "Additional Information", 
            use the **"resume_text"** and other profile info to generate a short, professional answer.
        8.  For unknown fields or EEO questions (Gender, Race, Veteran) where the profile 
            doesn't specify, return "N/A" to skip them.
        """
        
        # Inject the candidate data using .format()
        profile_json_string = json.dumps(candidate_data, default=str)
        system_prompt = prompt_template.format(profile_json=profile_json_string)
        
        response = self.client.chat.completions.create(
            model="gpt-4o", # Smarter model for generating written answers
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Form Structure: {json.dumps(form_structure)}"}
            ]
        )
        
        return json.loads(response.choices[0].message.content)

    def _fill_form(self, form_structure, filled_values):
        """
        Phase 3: Execute Selenium actions with robust logic.
        """
        fields = form_structure.get("fields", [])
        
        for field in fields:
            label = field.get('label')
            selector = field.get('selector')
            field_type = field.get('type')
            
            # The HTML 'value' chosen by the AI (e.g., "false", "Male", "B")
            target_value = filled_values.get(label)

            if not label or not selector or not field_type:
                self.logger.warning(f"Skipping malformed field from AI: {field}")
                continue

            if not target_value or target_value == "N/A":
                self.logger.info(f"Skipping field: {label}")
                continue

            try:
                self.logger.info(f"Filling {label} ({field_type}) with value '{target_value}'...")
                
                # --- CASE 1: FILE UPLOAD ---
                if field_type == 'file':
                    # This selector must be for the <input type="file">
                    # We use visibility_of_element_located to ensure it's interactable
                    # Some sites hide it, so we use presence_of_element_located
                    element = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    element.send_keys(target_value)
                
                # --- CASE 2: DROPDOWNS (Select) ---
                elif field_type == 'select':
                    element = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    select = Select(element)
                    try:
                        # 1. Try to select by VALUE (most reliable)
                        select.select_by_value(target_value)
                    except NoSuchElementException:
                        # 2. Fallback: Try to select by VISIBLE TEXT
                        try:
                            select.select_by_visible_text(target_value)
                        except NoSuchElementException:
                             self.logger.warning(f"Could not find option '{target_value}' for {label}. Skipping.")

                # --- CASE 3: RADIO BUTTONS ---
                elif field_type == 'radio':
                    # The AI gives us the 'value' (e.g., "Yes" or "No" from the HTML).
                    # The 'selector' from Phase 1 is the 'name' attribute (e.g., "[name='...']").
                    
                    name_attr = None
                    if "[name='" in selector:
                        name_attr = selector.split("[name='")[1].split("']")[0]
                    
                    if name_attr:
                        option_selector = f"input[name='{name_attr}'][value='{target_value}']"
                        
                        element_to_click = self.wait.until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, option_selector))
                        )
                        
                        # Use JavaScript click (much more reliable for styled radio buttons)
                        self.driver.execute_script("arguments[0].click();", element_to_click)
                    else:
                        self.logger.error(f"Could not parse 'name' from radio selector: {selector}")

                # --- CASE 4: STANDARD TEXT INPUTS ---
                else:
                    element = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    element.clear()
                    element.send_keys(str(target_value))
                    
            except Exception as e:
                self.logger.error(f"Failed to fill '{label}' (Selector: '{selector}', Value: '{target_value}'): {e}")

    def apply(self, job_url, candidate_data):
        try:
            self.logger.info(f"Navigating to job application: {job_url}")
            self.driver.get(job_url)
            self.wait.until(lambda d: d.execute_script('return document.readyState') == 'complete')
            time.sleep(2) 
            
            # 1. Scrape & Analyze (or load cache)
            html = self.driver.page_source
            structure = self._analyze_page_structure(job_url, html)
            
            if not structure.get("fields"):
                self.logger.error("Phase 1 Failed: AI did not return any fields to fill.")
                return False
                
            # 2. Generate Content
            filled_data = self._generate_field_values(structure, candidate_data)
            self.logger.debug(f"AI Fill Plan: {filled_data}")
            
            # 3. Fill
            self._fill_form(structure, filled_data)
            
            self.logger.info("Form filled successfully.")

            # --- *** NEW: SUBMIT LOGIC *** ---
            submit_info = structure.get("submit_button")
            if not submit_info or not submit_info.get("selector"):
                self.logger.error("AI did not find a submit button. Pausing for manual submission.")
                breakpoint() # Pause script for user
                return True # Assume user submitted manually

            submit_selector = submit_info.get("selector")
            submit_text = submit_info.get("text", "N/A")

            self.logger.info(f"Found submit button with text: '{submit_text}'")
            self.logger.warning(">>> PAUSING FOR FINAL REVIEW. <<<")
            self.logger.warning(f"Script will click button with selector: {submit_selector}")
            self.logger.warning("Inspect the browser. If correct, type 'c' and [Enter] in your debugger to submit.")
            
            breakpoint() # <-- SAFETY BREAKPOINT. Type 'c' to continue.

            try:
                submit_element = self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, submit_selector))
                )
                self.driver.execute_script("arguments[0].click();", submit_element)
                self.logger.info("--- SUBMITTED APPLICATION ---")
                time.sleep(5) # Wait for next page
                
            except Exception as e:
                self.logger.error(f"Failed to click submit button: {e}")
                self.logger.error("Pausing for manual submission.")
                breakpoint() # Pause if click fails

            return True
            
        except Exception as e:
            self.logger.error(f"Generic Apply failed: {e}")
            breakpoint() # Pause on any major error
            return False


# ... inside your __main__ block ...

if __name__ == "__main__":
    # Define your "rich" candidate data for the AI
    cv_data = _extract_pdf_text(os.path.abspath("resume.pdf"))
    candidate_data = {
        "first_name": "Birehan",
        "last_name": "Zewdie",
        "email": "birehananteneh4@gmail.com",
        "phone": "+251982070195",
        "linkedin": "linkedin.com/in/birehan",
        "resume_path": os.path.abspath("resume.pdf"),
        "skills": ["Python", "Selenium", "AI"],
        "experience_summary": "5 years in AI Engineering...",
        "visa_sponsorship": "Yes, I am Ethiopia Citizen",
        "start_date": "Immediately",
        'cv_data': cv_data
    }
    

    agent = JobScraperAgent(
    )
    
    scrape_input = ScraperInput(
                site_type=[Site.LINKEDIN],
                search_term='AI Engineer',
                country = Country.WORLDWIDE,
                location='worldwide',
                is_remote=True,
                # job_type=JobType.FULL_TIME,
                easy_apply=False,
                hours_old=24,
                results_wanted=100,
                experience_level=[ExperienceLevel.ENTRY_LEVEL, ExperienceLevel.ASSOCIATE, ExperienceLevel.MID_SENIOR_LEVEL]
    )
    
    jobs = agent.find_jobs(scrape_input)
    breakpoint()
    
    # results = []
    # not_easy_apply_jobs = []
    # for job  in jobs:
    #     result = agent.get_job_details_by_id(job.id) #4296340639
    #     results.append(result)
    #     if result['apply_type'] != 'Easy Apply':
    #         not_easy_apply_jobs.append(result)
    
    
    applicator = LLMGenericApplicator(agent.driver, agent.openai_client)
    not_easy_apply_jobs = ["https://jobs.lever.co/USMobile/7800a658-f3a0-4e5f-a023-4dcf23a6b449/apply?lever-source=LinkedIn&source=LinkedIn"]
    for job in not_easy_apply_jobs:
        applicator.apply(job, candidate_data)
        
        print("--> Paused. Please click 'Submit' manually, then press Enter.")
        input()