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
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, TimeoutException
# --- NEW IMPORTS for Explicit Waits ---
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from typing import Any, Dict, Optional, List
import re
from datetime import datetime, timedelta
from urllib.parse import unquote
from job_agent.linkedin.model import Country, JobType, ScraperInput, Site, JobResponse

from job_agent.linkedin.job_search import JobSearch
logger = logging.getLogger(__name__)

# --- Configuration ---

# 1. Set your OpenAI API Key as an environment variable:
#    export OPENAI_API_KEY='your_key_here'
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise Exception("OPENAI_API_KEY environment variable not set.")

# 2. Your Google Sheets credentials file (from Google Cloud Console)

# 3. The exact name of the Google Sheet you created and shared
GOOGLE_SHEET_NAME = "My AI Job Tracker"

# 4. A detailed summary of your CV.
MY_CV_SUMMARY = """
Experienced Data Scientist with 5+ years in machine learning, NLP, and data visualization.
Proficient in Python (Pandas, Scikit-learn, TensorFlow), SQL, and R.
Proven track record of developing predictive models for e-commerce, reducing churn by 15%.
Strong communicator, skilled in translating complex data insights for non-technical stakeholders.
M.S. in Data Science.
Looking for remote-first roles in AI/ML Engineering or Senior Data Science.
"""

# 5. The site configuration.
JOB_SITES_CONFIG = [
    {
        "name": "LinkedIn",
        "search_url": "https://www.linkedin.com/jobs/search/?keywords=machine%20learning%20engineer&location=United%20States&f_WT=2&geoId=103644278&f_TPR=r86400",
        "job_card_selector": "div.base-search-card",
        "job_link_selector_within_card": "a.base-card__full-link",
        "company_name_selector": "h4.base-search-card__subtitle",
        "job_description_selector": "div.description__text"
    }
]

# 6. Cookie file for LinkedIn (must be in the same folder)
LINKEDIN_COOKIE_FILE = "cookies.json"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
import platform
from selenium.webdriver.chrome.options import Options

def get_default_user_agent() -> str:
    """Get platform-specific default user agent to reduce fingerprinting."""
    system = platform.system()

    if system == "Windows":
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    elif system == "Darwin":  # macOS
        return "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    else:  # Linux and others
        return "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"


def create_chrome_options() -> Options:
    """
    Create Chrome options with all necessary configuration for LinkedIn scraping.

    Args:
        config: AppConfig instance with Chrome configuration

    Returns:
        Options: Configured Chrome options object
    """
    chrome_options = Options()

 
    # if config.chrome.headless:
    chrome_options.add_argument("--headless=new")

    # Add essential options for stability
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-default-apps")
    chrome_options.add_argument("--disable-sync")
    chrome_options.add_argument("--metrics-recording-only")
    chrome_options.add_argument("--no-default-browser-check")
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees")
    chrome_options.add_argument("--aggressive-cache-discard")
    chrome_options.add_argument("--disable-ipc-flooding-protection")

    # Set user agent (configurable with platform-specific default)
    user_agent = get_default_user_agent()
    chrome_options.add_argument(f"--user-agent={user_agent}")

    return chrome_options


def find_chromedriver() -> Optional[str]:
    """Find the ChromeDriver executable in common locations."""
    # First check environment variable
    if path := os.getenv("CHROMEDRIVER"):
        if os.path.exists(path):
            return path
        
def create_chrome_service():
    # Use ChromeDriver path from environment or config
    chromedriver_path = (
        find_chromedriver()
    )

    if chromedriver_path:
        logger.info(f"Using ChromeDriver at path: {chromedriver_path}")
        return Service(executable_path=chromedriver_path)
    else:
        logger.info("Using auto-detected ChromeDriver")
        return None
    
def create_chrome_driver() -> webdriver.Chrome:

    logger.info("Initializing Chrome WebDriver...")

    # Create Chrome options using shared function
    chrome_options = create_chrome_options()

    # Create Chrome service using shared function
    service = create_chrome_service()

    # Initialize Chrome driver
    if service:
        driver = webdriver.Chrome(service=service, options=chrome_options)
    else:
        driver = webdriver.Chrome(options=chrome_options)

    logger.info("Chrome WebDriver initialized successfully")

    # Add a page load timeout for safety
    driver.set_page_load_timeout(60)

    # Set shorter implicit wait for faster cookie validation
    driver.implicitly_wait(10)

    return driver


def login_with_cookie(driver: webdriver.Chrome, cookie: str) -> bool:
    import time

    try:
        from job_agent.linkedin import actions  # type: ignore
        from selenium.common.exceptions import TimeoutException

        logger.info("Attempting cookie authentication...")
        driver.set_page_load_timeout(45)

        # Attempt login
        retry_count = 0
        max_retries = 1

        while retry_count <= max_retries:
            try:
                actions.login(driver, cookie=cookie)
                # If we reach here without timeout, login attempt completed
                break
            except TimeoutException:
                # Timeout indicates invalid cookie (page loads forever)
                logger.warning(
                    "Cookie authentication failed - page load timeout (likely invalid cookie)"
                )
                return False
            except Exception as e:
                # Handle InvalidCredentialsError from linkedin-scraper
                # This library sometimes incorrectly reports failure even when login succeeds
                if "InvalidCredentialsError" in str(
                    type(e)
                ) or "Cookie login failed" in str(e):
                    logger.info(
                        "LinkedIn-scraper reported InvalidCredentialsError - verifying actual authentication status..."
                    )
                    # Give LinkedIn time to complete redirect
                    time.sleep(2)
                    break
                else:
                    logger.warning(f"Login attempt failed: {e}")
                    if retry_count < max_retries:
                        retry_count += 1
                        logger.info(
                            f"Retrying authentication (attempt {retry_count + 1}/{max_retries + 1})"
                        )
                        time.sleep(2)
                        continue
                    else:
                        return False

        # Check authentication status by examining the current URL
        try:
            current_url = driver.current_url

            # Check if we're on login page (authentication failed)
            if "login" in current_url or "uas/login" in current_url:
                logger.warning(
                    "Cookie authentication failed - redirected to login page"
                )
                return False

            # Check if we're on authenticated pages (authentication succeeded)
            elif any(
                indicator in current_url
                for indicator in ["feed", "mynetwork", "linkedin.com/in/", "/feed/"]
            ):
                logger.info("Cookie authentication successful")
                return True

            # Unexpected page - wait briefly and check again
            else:
                logger.info(
                    "Unexpected page after login, checking authentication status..."
                )
                time.sleep(2)

                final_url = driver.current_url
                if "login" in final_url or "uas/login" in final_url:
                    logger.warning("Cookie authentication failed - ended on login page")
                    return False
                elif any(
                    indicator in final_url
                    for indicator in ["feed", "mynetwork", "linkedin.com/in/", "/feed/"]
                ):
                    logger.info("Cookie authentication successful after verification")
                    return True
                else:
                    logger.warning(
                        f"Cookie authentication uncertain - unexpected final page: {final_url}"
                    )
                    return False

        except Exception as e:
            logger.error(f"Error checking authentication status: {e}")
            return False

    except Exception as e:
        logger.error(f"Cookie authentication failed with error: {e}")
        return False
    finally:
        # Restore normal timeout
        driver.set_page_load_timeout(60)
        
def login_to_linkedin(driver: webdriver.Chrome, authentication: str) -> None:
    # Try cookie authentication
    if login_with_cookie(driver, authentication):
        logger.info("Successfully logged in to LinkedIn using cookie")
        return

    logger.error("Cookie authentication failed")
    logger.info("Cleared invalid cookie from authentication storage")

    try:
        current_url: str = driver.current_url

        if "checkpoint/challenge" in current_url:
            if "security check" in driver.page_source.lower():
                raise SecurityChallengeError(
                    challenge_url=current_url,
                    message="LinkedIn requires a security challenge. Please complete it manually and restart the application.",
                )
            else:
                raise CaptchaRequiredError(captcha_url=current_url)
        else:
            raise InvalidCredentialsError(
                "Cookie authentication failed - cookie may be expired or invalid"
            )

    except Exception as e:
        raise LoginTimeoutError(f"Login failed: {str(e)}")
    
def get_or_create_driver(authentication: str) -> webdriver.Chrome:
    session_id = "default"  # We use a single session for simplicity

    # Return existing driver if available
    # if session_id in active_drivers:
    #     logger.info("Using existing Chrome WebDriver session")
    #     return active_drivers[session_id]

    try:
        driver = create_chrome_driver()
        login_to_linkedin(driver, authentication)
        logger.info("Chrome WebDriver session created and authenticated successfully")
        return driver
    except Exception as e:
        logger.error(f"error creating driver: {e}")
        pass
    

def parse_relative_date(date_str):
    if "just now" in date_str.lower():
        return datetime.now()

    # Regex to find the number and the unit (e.g., "1", "week")
    match = re.search(r'(\d+)\s+(\w+)', date_str)
    
    if match:
        qty = int(match.group(1))
        unit = match.group(2).lower() # e.g., "week", "days", "hours"
        
        if 'minute' in unit:
            delta = timedelta(minutes=qty)
        elif 'hour' in unit:
            delta = timedelta(hours=qty)
        elif 'day' in unit:
            delta = timedelta(days=qty)
        elif 'week' in unit:
            delta = timedelta(weeks=qty)
        elif 'month' in unit:
            delta = timedelta(days=qty * 30) # Approx
        elif 'year' in unit:
            delta = timedelta(days=qty * 365)
        else:
            delta = timedelta(seconds=0)
            
        return datetime.now() - delta
    
    return None # Fallback if format is unexpected



        
class JobScraperAgent:
    """
    An AI agent that scrapes job sites, analyzes job descriptions against a CV,
    and logs suitable jobs to a Google Sheet.
    """
    def __init__(self, cv_summary=MY_CV_SUMMARY, sheet_name=GOOGLE_SHEET_NAME):
        self.cv_summary = cv_summary
        self.openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        self.driver = get_or_create_driver(os.environ.get("LINKEDIN_COOKIE", ""))
        self.wait = WebDriverWait(self.driver, 10)

    
    def scrape_company_location_stats(self, company_url: str) -> Dict[str, int]:
        logging.info(f"Starting location stats scrape for: {company_url}")
        
        base_url_match = re.search(
            r'^(https?://(?:www\.)?linkedin\.com/company/[^/]+)', 
            company_url
        )

        if not base_url_match:
            logging.error(f"Invalid company URL format: {company_url}. Expected '.../company/company-name/'.")
            return {}
            
        base_company_url = base_url_match.group(1)
        
        # Ensure it has a trailing slash for consistency
        if not base_company_url.endswith('/'):
            base_company_url += '/'
            
        people_url = f"{base_company_url}people/"
        # --- END OF FIX ---

        locations_data: Dict[str, int] = {}

        try:
            # 2. Navigate to the 'people' page
            self.driver.get(people_url)
            logging.info(f"Navigated to: {people_url}")
            time.sleep(10)
            
            # insight-container
            html_source = self.driver.page_source
            soup = BeautifulSoup(html_source, 'html.parser')
            time.sleep(3)
            location_container = soup.find('div', class_='org-people-bar-graph-module__geo-region')

            # 3. Create an empty list to store the results

            # 4. Check if the container was found before trying to search inside it
            if location_container:
                # 5. Now, find all the <button> elements *only within that container*
                location_entries = location_container.find_all(
                    'button', class_='org-people-bar-graph-element'
                )

                # 6. Loop through each entry found
                for entry in location_entries:
                    count_tag = entry.find('strong')
                    location_tag = entry.find('span', class_='org-people-bar-graph-element__category')

                    if count_tag and location_tag:
                        count = count_tag.text.strip()
                        location = location_tag.text.strip()
                        
                        locations_data[location] = int(count)
            
            else:
                logger.error("Error: Could not find the location container.")

            # 7. Print the final list
            return locations_data
            
        except Exception as e:
            logging.error(f"An unexpected error occurred during location scraping: {e}")
            return {}
        
    def _load_linkedin_cookie(self):
        """Loads a LinkedIn session cookie from a file to bypass login."""
        if not os.path.exists(LINKEDIN_COOKIE_FILE):
            logging.warning(f"LinkedIn cookie file not found: {LINKEDIN_COOKIE_FILE}")
            logging.warning("Proceeding without being logged in. Selectors may fail.")
            return

        try:
            logging.info("Loading LinkedIn session cookie...")
            
            # --- FIX: Go to www.linkedin.com to match the cookie's domain ---
            self.driver.get("https://www.linkedin.com")
            
            with open(LINKEDIN_COOKIE_FILE, 'r') as f:
                cookies = json.load(f)
            
            for cookie in cookies:
                if 'expirationDate' in cookie:
                    cookie['expiry'] = int(cookie['expirationDate'])
                    del cookie['expirationDate']

                if 'sameSite' in cookie and cookie['sameSite'] not in ['Strict', 'Lax', 'None']:
                    logging.warning(f"Removing invalid 'sameSite' value: {cookie['sameSite']}")
                    del cookie['sameSite']

                try:
                    self.driver.add_cookie(cookie)
                except Exception as e:
                    logging.warning(f"Could not add cookie {cookie.get('name')}: {e}")

            logging.info("Cookie loaded successfully. Refreshing page as logged-in user.")
            # Refresh the page to be in a "logged in" state
            # self.driver.refresh()
            # time.sleep(2) # Wait for refresh
        
        except Exception as e:
            logging.error(f"Failed to load cookies: {e}")

    def _check_linkedin_login(self):
        """
        Validates if the user is logged in by checking for a known logged-in-only element.
        This should be called *after* loading a LinkedIn page.
        """
        try:
            # This selector targets the "Me" profile picture icon in the top nav bar.
            # It's a strong indicator of being logged in.
            me_icon_selector = "img.global-nav__me-photo"
            
            # Use a shorter wait time just for this check
            check_wait = WebDriverWait(self.driver, 5) # 5 seconds
            
            logging.info("Validating LinkedIn login status...")
            check_wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, me_icon_selector))
            )
            # If the element is found without a timeout, we are logged in.
            logging.info("Login check successful. User is logged in.")
            return True
        except TimeoutException:
            # This is the expected failure if not logged in
            logging.warning("Login check FAILED. 'Me' icon not found. Cookie may be invalid or expired.")
            logging.warning("Scraping will continue in a logged-out state (selectors may fail).")
            return False
        except Exception as e:
            # Other unexpected errors
            logging.error(f"An error occurred during login check: {e}")
            return False
        
    def _get_page_and_wait(self, url, selector_to_wait_for):
        """
        Fetches a URL and waits for a specific element to be present
        before returning the page source.
        """
        try:
            self.driver.get(url)
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector_to_wait_for))
            )
            return self.driver.page_source
        except TimeoutException:
            logging.error(f"Timeout waiting for element '{selector_to_wait_for}' on {url}")
            return None
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
                return soup.body.get_text(separator=' ', strip=True) # type:ignore
        except Exception as e:
            logging.error(f"Error parsing HTML: {e}")
            return ""

    def close(self):
        """Closes the browser session."""
        logging.info("Shutting down driver...")
        if self.driver:
            self.driver.quit()

    def get_job_details_by_id(self, job_id):
        """
        Navigates directly to a specific job ID and extracts detailed metadata.
        """
        url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        logging.info(f"Fetching details for Job ID: {job_id}...")

        try:
            self.driver.get(url)
            try:
                # Wait for the description or the job header to load
                self.wait.until(EC.presence_of_element_located((By.XPATH, "//*[@data-testid='expandable-text-box']")))
                time.sleep(2) 
            except Exception as e:
                logging.error(f"Page took too long to load: {e}")
            
            html_source = self.driver.page_source
            soup = BeautifulSoup(html_source, 'html.parser')

            # --- 1. EXTRACT TITLE ---
            # Ideally, get the H1 directly rather than the page title tag
            h1_tag = soup.find('h1')
            job_title = h1_tag.get_text(strip=True) if h1_tag else "N/A"
            
            # Fallback to title tag if H1 fails
            if job_title == "N/A" and soup.title:
                job_title = soup.title.string and soup.title.string.split("|")[0].strip()

            # --- 2. EXTRACT COMPANY NAME & URL (FIXED) ---
            company_name = "N/A"
            company_linkedin_url = "N/A"

            # Find the anchor tag containing '/company/' in the href
            # We iterate to find the one that actually has text (the name), skipping the logo link if separate
            company_links = soup.find_all('a', href=re.compile(r'/company/'))
            
            for link in company_links:
                link_text = link.get_text(strip=True)
                # We prioritize the link that has text content (e.g., "Crossing Hurdles")
                if link_text:
                    company_name = link_text
                    company_linkedin_url = link['href']
                    break
            
            # If we found a link but it had no text (just a logo), try to grab the URL at least
            if company_linkedin_url == "N/A" and company_links:
                company_linkedin_url = company_links[0]['href']

            # --- 3. EXTRACT DESCRIPTION ---
            desc_tag = soup.find(attrs={"data-testid": "expandable-text-box"})
            if not desc_tag:
                # Fallback for different page structures
                desc_tag = soup.find(id="job-details")
            
            description = desc_tag.get_text(separator="\n").strip() if desc_tag else "N/A"

            # --- 4. METADATA (Posted date, Applicants) ---
            metadata_text = ""
            main_content = soup.find('main')
            if main_content:
                # Look for the list of job insights (often styled as <li> or specific classes)
                # Broad approach: grab text from the top card area
                top_card = soup.find('div', class_=lambda x: x and 'top-card' in x)
                if top_card:
                    metadata_text = top_card.get_text(separator=" · ")
                else:
                    # Fallback to your original method
                    p_tags = main_content.find_all('p')
                    for p in p_tags:
                        if "ago" in p.get_text():
                            metadata_text = p.get_text()
                            break

            # Parse metadata text
            posted_date_str = "N/A"
            applicants_count = 0
            
            # Normalize text to split easier
            parts = metadata_text.replace('\n', ' ').split('·')

            for part in parts:
                part = part.strip()
                if any(x in part for x in ["ago", "minute", "hour", "day", "week", "month"]):
                    posted_date_str = part
                elif any(x in part for x in ["applicant", "people", "apply"]):
                    numbers = re.findall(r'\d+', part)
                    applicants_count = int(numbers[0]) if numbers else 0

            posted_date = parse_relative_date(posted_date_str) # Ensure this helper function exists in your class

            # --- 5. APPLY BUTTON ---
            job_application_url = "N/A"
            apply_type = "Easy Apply" 

            apply_btn = soup.find(attrs={"data-view-name": "job-apply-button"})

            if apply_btn:
                raw_url = apply_btn.get('href', '')
                btn_text = apply_btn.get_text(separator=" ").strip().lower()
                if "easy apply" in btn_text:
                    apply_type = "Easy Apply"
                    job_application_url = raw_url
                else:
                    apply_type = "External Apply"
                    # LinkedIn wraps external URLs, try to clean it
                    if "url=" in raw_url:
                        try:
                            # You need to import unquote: from urllib.parse import unquote
                            job_application_url = unquote(raw_url.split("url=")[1].split("&")[0])
                        except:
                            job_application_url = raw_url
                    else:
                        job_application_url = raw_url

            job_details = {
                "job_id": job_id,
                "job_title": job_title,
                "company_name": company_name,
                "company_linkedin_url": company_linkedin_url,
                "posted_date": posted_date,
                "applicants_count": applicants_count,
                "description": description,
                "url": url,
                "apply_type": apply_type,
                "job_application_url": job_application_url
            }
            
            logging.info(f"Successfully extracted details for {job_title}")
            return job_details

        except TimeoutException:
            logging.error(f"Timeout: Page for Job ID {job_id} did not load correctly.")
            return None
        except Exception as e:
            logging.error(f"Error parsing Job ID {job_id}: {e}")
            return None
        
    # def get_job_details_by_id(self, job_id):
    #     """
    #     Navigates directly to a specific job ID and extracts detailed metadata.
    #     """
    #     url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    #     logging.info(f"Fetching details for Job ID: {job_id}...")

    #     try:
    #         self.driver.get(url)
    #         try:
    #             self.wait.until(EC.presence_of_element_located((By.XPATH, "//*[@data-testid='expandable-text-box']")))
    #             time.sleep(2) 
    #         except Exception as e:
    #             logging.error(f"Page took too long to load: {e}")
    #         html_source = self.driver.page_source
    #         soup = BeautifulSoup(html_source, 'html.parser')

    #         full_title = soup.title.string if soup.title else ""
    #         if full_title and "|" in full_title :
    #             job_title = full_title.split("|")[0].strip()
    #             company_name = full_title.split('|')[1].strip()
    #         else:
    #             job_title = full_title
    #             company_name = "N/A"

    #         main_content = soup.find('main')
    #         company_link_tag = main_content.find('a', href=re.compile(r'/company/')) if main_content else None
    #         company_linkedin_url = company_link_tag['href'] if company_link_tag else "N/A"

    #         desc_tag = soup.find(attrs={"data-testid": "expandable-text-box"})
    #         description = desc_tag.get_text(separator="\n").strip() if desc_tag else "N/A"

    #         metadata_text = ""
    #         if main_content:
    #             p_tags = main_content.find_all('p')
    #             for p in p_tags:
    #                 if "ago" in p.get_text():
    #                     metadata_text = p.get_text()
    #                     break

    #         parts = metadata_text.split('·')
    #         posted_date_str = "N/A"
    #         applicants_count = 0

    #         for part in parts:
    #             part = part.strip()
    #             if any(x in part for x in ["ago", "minute", "hour", "day", "week", "month"]):
    #                 posted_date_str = part
    #             elif any(x in part for x in ["applicant", "people", "apply"]):
    #                 numbers = re.findall(r'\d+', part)
    #                 applicants_count = int(numbers[0]) if numbers else 0

    #         posted_date = parse_relative_date(posted_date_str)
    #         job_application_url = "N/A"
    #         apply_type = "Easy Apply" # Default

    #         apply_btn = soup.find(attrs={"data-view-name": "job-apply-button"})

    #         if apply_btn:
    #             raw_url = apply_btn.get('href', '')
    #             btn_text = apply_btn.get_text(separator=" ").strip().lower()
    #             if "easy apply" in btn_text:
    #                 apply_type = "Easy Apply"
    #                 job_application_url = raw_url
    #             else:
    #                 apply_type = "External Apply"
    #                 if "url=" in raw_url:
    #                     try:
    #                         job_application_url = unquote(raw_url.split("url=")[1].split("&")[0])
    #                     except:
    #                         job_application_url = raw_url
    #                 else:
    #                     job_application_url = raw_url

    #         job_details = {
    #             "job_id": job_id,
    #             "job_title": job_title,
    #             "company_name": company_name,
    #             "company_linkedin_url": company_linkedin_url,
    #             "posted_date": posted_date,
    #             "applicants_count": applicants_count,
    #             "description": description,
    #             "url": url,
    #             "apply_type": apply_type,
    #             "job_application_url": job_application_url
    #         }
            
    #         logging.info(f"Successfully extracted details for {job_title}")
    #         return job_details

    #     except TimeoutException:
    #         logging.error(f"Timeout: Page for Job ID {job_id} did not load correctly.")
    #         return None
    #     except Exception as e:
    #         logging.error(f"Error parsing Job ID {job_id}: {e}")
    #         return None

    
    def find_jobs(self, scraper_input: ScraperInput) -> JobResponse:
        try:
            job_search = JobSearch(driver=self.driver, close_on_complete=False, scrape=False)
            jobs = job_search.search(scraper_input)
            return jobs
        except Exception as e:
            logging.error(f"find_jobs error: {e}")            
            return JobResponse(jobs=[])


if __name__ == "__main__":
    agent = JobScraperAgent(
    )
    
    scrape_input = ScraperInput(
                site_type=[Site.LINKEDIN],
                search_term='AI Engineer',
                country = Country.WORLDWIDE,
                location='worldwide',
                is_remote=True,
                easy_apply=False,
                hours_old=24,
                results_wanted=100
    )
    
    jobs = agent.find_jobs(scrape_input)
    
    try:
        results = []
        not_easy_apply_jobs = []
        for job  in jobs.jobs:
            result = agent.get_job_details_by_id(job.id)
            results.append(result)
            if result['apply_type'] != 'Easy Apply':
                not_easy_apply_jobs.append(result)
        breakpoint()
    except Exception as e:
        breakpoint()
        logging.error(f"An unexpected error occurred: {e}")
    finally:
        agent.close()
