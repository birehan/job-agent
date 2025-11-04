import os
import time
import json
import logging
import gspread
import openai
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
# Import the new webdriver_manager
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from bs4 import BeautifulSoup

# --- Configuration ---

# 1. Set your OpenAI API Key as an environment variable:
#    export OPENAI_API_KEY='your_key_here'
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise EnvironmentError("OPENAI_API_KEY environment variable not set.")

# 2. Your Google Sheets credentials file (from Google Cloud Console)
GOOGLE_CREDS_JSON = "/root/birehan/job-agent/data/job-agent-477209-1922ceed7505.json" 

# 3. The exact name of the Google Sheet you created and shared
GOOGLE_SHEET_NAME = "My AI Job Tracker" 

# 4. A detailed summary of your CV. The more detail, the better the AI's analysis.
MY_CV_SUMMARY = """
Experienced Data Scientist with 5+ years in machine learning, NLP, and data visualization. 
Proficient in Python (Pandas, Scikit-learn, TensorFlow), SQL, and R. 
Proven track record of developing predictive models for e-commerce, reducing churn by 15%. 
Strong communicator, skilled in translating complex data insights for non-technical stakeholders. 
M.S. in Data Science. 
Looking for remote-first roles in AI/ML Engineering or Senior Data Science.
"""

# 5. The site configuration.
# ⚠️ WARNING: THESE SELECTORS ARE EXAMPLES AND WILL BREAK. 
# You MUST find the correct, current selectors manually using browser "Inspect Element".
JOB_SITES_CONFIG = [
    {
        "name": "LinkedIn",
        "search_url": "https://www.linkedin.com/jobs/search/?keywords=machine%20learning%20engineer&location=United%20States&f_WT=2&geoId=103644278",
        # Example selector (will break)
        "job_link_selector": "a.base-card__full-link", 
        # Example selector (will break)
        "job_description_selector": "div.description__text" 
    },
    {
        "name": "Indeed",
        "search_url": "https://www.indeed.com/jobs?q=data+scientist&l=Remote",
        # Example selector (will break)
        "job_link_selector": "a.jcs-JobTitle",
        # Example selector (will break)
        "job_description_selector": "div#jobDescriptionText"
    },
    # {
    #     "name": "Kaggle Jobs",
    #     "search_url": "https://www.kaggle.com/jobs",
    #     "job_link_selector": "a.job-card-link",
    #     "job_description_selector": "div.job-description"
    # },
    # ... Add all 10 sites here with their correct URLs and selectors
]

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class JobScraperAgent:
    """
    An AI agent that scrapes job sites, analyzes job descriptions against a CV,
    and logs suitable jobs to a Google Sheet.
    """
    def __init__(self, cv_summary, creds_file, sheet_name):
        self.cv_summary = cv_summary
        self.openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        self.driver = self._setup_driver()
        self.sheet = self._setup_google_sheets(creds_file, sheet_name)
        self._setup_sheet_headers()

    def _setup_driver(self):
        """Initializes a headless Chrome WebDriver."""
        logging.info("Setting up headless Chrome driver...")
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        
        # --- FIX: Added these two lines ---
        # This is critical for running as 'root' in Docker/server environments
        chrome_options.add_argument("--no-sandbox")
        # This prevents crashes related to limited shared memory in containers
        chrome_options.add_argument("--disable-dev-shm-usage") 
        
        chrome_options.add_argument("--window-size=1920,1200")
        # Act like a real browser
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36"
        )
        
        try:
            # --- FIX: Use ChromeDriverManager to automatically get the correct driver ---
            # This line will download the correct chromedriver if you don't have it,
            # or find the one that matches your installed Chrome browser.
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            return driver
        except Exception as e:
            logging.error(f"Failed to initialize Selenium WebDriver: {e}")
            logging.error("Please ensure you have Google Chrome browser *itself* installed on this machine.")
            raise

    def _setup_google_sheets(self, creds_file, sheet_name):
        """Connects to Google Sheets API and opens the correct sheet."""
        try:
            logging.info(f"Connecting to Google Sheets: {sheet_name}...")
            gc = gspread.service_account(filename=creds_file)
            sh = gc.open(sheet_name)
            worksheet = sh.sheet1
            return worksheet
        except gspread.exceptions.SpreadsheetNotFound:
            logging.error(f"Spreadsheet '{sheet_name}' not found.")
            logging.error("Did you create it and share it with the service account email?")
            raise
        except Exception as e:
            logging.error(f"Failed to connect to Google Sheets: {e}")
            raise

    def _setup_sheet_headers(self):
        """Adds headers to the Google Sheet if it's empty."""
        try:
            if not self.sheet.get('A1').value:
                logging.info("Setting up sheet headers...")
                headers = ["Timestamp", "Job Title", "Company", "Job URL", "Fit (Yes/No)", "Confidence", "Reasoning", "Missing Keywords"]
                self.sheet.append_row(headers)
        except Exception as e:
            logging.warning(f"Could not set up sheet headers: {e}")

    def _get_page_content(self, url):
        """Fetches a URL and returns the page HTML source."""
        try:
            self.driver.get(url)
            # Wait for basic dynamic content to load.
            # A more robust solution would use Selenium's explicit waits.
            time.sleep(5) 
            return self.driver.page_source
        except Exception as e:
            logging.error(f"Error fetching URL {url}: {e}")
            return None

    def _extract_text_content(self, html, selector):
        """Uses BeautifulSoup to extract clean text from a specific part of the page."""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            content_block = soup.select_one(selector)
            if content_block:
                return content_block.get_text(separator=' ', strip=True)
            else:
                logging.warning(f"Could not find description block with selector: {selector}")
                # Fallback to body if selector fails
                return soup.body.get_text(separator=' ', strip=True)
        except Exception as e:
            logging.error(f"Error parsing HTML: {e}")
            return ""

    def check_job_fit(self, job_description_text):
        """Uses GPT-4o-mini to analyze the job description against the CV."""
        if not job_description_text:
            return None

        system_prompt = """
        You are an expert HR recruitment assistant. Your task is to analyze a job description (JD) 
        against a candidate's CV summary. Respond ONLY with a valid JSON object with the 
        following structure:
        {
          "is_fit": boolean,
          "reason": "A brief 1-2 sentence explanation for your decision.",
          "confidence_score": float (0.0 to 1.0),
          "missing_keywords": ["list", "of", "key", "skills", "from", "JD", "not", "in", "CV"]
        }
        """
        
        user_prompt = f"""
        Candidate CV Summary:
        ---
        {self.cv_summary}
        ---

        Job Description:
        ---
        {job_description_text[:4000]} 
        ---

        Analyze the fit and provide your JSON response.
        """

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"}
            )
            
            result_json = response.choices[0].message.content
            return json.loads(result_json)
        
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse JSON response from AI: {e}\nResponse: {result_json}")
            return None
        except Exception as e:
            logging.error(f"Error calling OpenAI API: {e}")
            return None

    def track_in_google_sheets(self, job_data, analysis):
        """Logs the job and its analysis to the Google Sheet."""
        try:
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            is_fit_str = "Yes" if analysis.get('is_fit', False) else "No"
            confidence = analysis.get('confidence_score', 'N/A')
            reason = analysis.get('reason', 'N/A')
            missing_keywords = ", ".join(analysis.get('missing_keywords', []))
            
            row = [
                timestamp,
                job_data.get('title', 'N/A'),
                job_data.get('company', 'N/A'), # Note: We aren't scraping company yet
                job_data.get('url', 'N/A'),
                is_fit_str,
                confidence,
                reason,
                missing_keywords
            ]
            
            self.sheet.append_row(row)
            logging.info(f"Logged job to Google Sheets: {job_data.get('title')}")

        except Exception as e:
            logging.error(f"Failed to log to Google Sheets: {e}")

    def scrape_site(self, site_config):
        """Scrapes a single site based on its configuration."""
        name = site_config["name"]
        search_url = site_config["search_url"]
        job_link_selector = site_config["job_link_selector"]
        job_desc_selector = site_config["job_description_selector"]

        logging.info(f"--- Starting scrape for {name} ---")
        
        search_page_html = self._get_page_content(search_url)
        if not search_page_html:
            logging.error(f"Failed to get search page for {name}. Skipping.")
            return

        try:
            # We use driver.find_elements here because the page is already loaded
            job_elements = self.driver.find_elements(By.CSS_SELECTOR, job_link_selector)
            logging.info(f"Found {len(job_elements)} job links on {name}.")
            
            job_links = []
            for elem in job_elements:
                try:
                    href = elem.get_attribute('href')
                    title = elem.text
                    if href:
                        job_links.append({"url": href, "title": title})
                except StaleElementReferenceException:
                    continue # Element disappeared, just skip it
            
            # Limit to first 5 to avoid being blocked
            for job in job_links[:5]: 
                job_url = job['url']
                job_title = job['title']
                
                logging.info(f"Scraping job: {job_title} ({job_url})")
                job_page_html = self._get_page_content(job_url)
                if not job_page_html:
                    continue

                job_text = self._extract_text_content(job_page_html, job_desc_selector)
                
                analysis = self.check_job_fit(job_text)
                
                if analysis:
                    logging.info(f"Analysis for {job_title}: Fit: {analysis.get('is_fit')}, Confidence: {analysis.get('confidence_score')}")
                    # if analysis.get('is_fit'):
                    self.track_in_google_sheets(job, analysis)
                
                # Be a good citizen and don't scrape too fast
                time.sleep(3) 

        except NoSuchElementException:
            logging.error(f"CRITICAL: CSS selector '{job_link_selector}' not found on {name}.")
            logging.error("This site's layout has changed. Please update the selector in JOB_SITES_CONFIG.")
        except Exception as e:
            logging.error(f"An error occurred while scraping {name}: {e}")

    def run(self, site_configs):
        """Runs the scraper agent for all configured sites."""
        logging.info("Starting AI Job Scraper Agent...")
        for config in site_configs:
            self.scrape_site(config)
        logging.info("All sites scraped.")

    def close(self):
        """Closes the browser session."""
        logging.info("Shutting down driver...")
        if self.driver:
            self.driver.quit()


if __name__ == "__main__":
    if not os.path.exists(GOOGLE_CREDS_JSON):
        logging.error(f"'{GOOGLE_CREDS_JSON}' not found.")
        logging.error("Please download your service account credentials and rename them.")
    else:
        agent = JobScraperAgent(
            cv_summary=MY_CV_SUMMARY,
            creds_file=GOOGLE_CREDS_JSON,
            sheet_name=GOOGLE_SHEET_NAME
        )
        
        try:
            agent.run(JOB_SITES_CONFIG)
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
        finally:
            agent.close()

