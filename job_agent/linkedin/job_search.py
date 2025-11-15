from datetime import datetime
import os
from typing import List, Optional
import urllib.parse
from time import sleep

from selenium.webdriver.common.by import By

from .model import Country, JobPost, JobResponse, Location, ScraperInput
from .util import create_logger, job_type_code, experience_level_code
from .jobs import Job
from .objects import Scraper
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
logger = create_logger(__name__)

class JobSearch(Scraper):
    AREAS = ["recommended_jobs", None, "still_hiring", "more_jobs"]

    def __init__(
        self,
        driver,
        base_url="https://www.linkedin.com/jobs/",
        close_on_complete=False,
        scrape=True,
        scrape_recommended_jobs=True,
    ):
        super().__init__()
        self.driver = driver
        self.base_url = base_url

        if scrape:
            self.scrape(close_on_complete, scrape_recommended_jobs)

    def scrape(self, close_on_complete=True, scrape_recommended_jobs=True):
        if self.is_signed_in():
            self.scrape_logged_in(
                close_on_complete=close_on_complete,
                scrape_recommended_jobs=scrape_recommended_jobs,
            )
        else:
            raise NotImplementedError("This part is not implemented yet")

    def scrape_job_card(self, base_element) -> Job:
        try:
            # Try to find job title and URL using updated selectors
            job_link = base_element.find_element(
                By.CLASS_NAME, "job-card-container__link"
            )
            job_title = job_link.text.strip()
            linkedin_url = job_link.get_attribute("href")

            # Find company name
            company = base_element.find_element(
                By.CLASS_NAME, "artdeco-entity-lockup__subtitle"
            ).text.strip()

            # Find location (try multiple possible selectors)
            location = ""
            try:
                location = base_element.find_element(
                    By.CLASS_NAME, "job-card-container__metadata-wrapper"
                ).text.strip()
            except:
                try:
                    location = base_element.find_element(
                        By.CLASS_NAME, "job-card-container__metadata-item"
                    ).text.strip()
                except:
                    location = "Location not found"

            job = Job(
                linkedin_url=linkedin_url,
                job_title=job_title,
                company=company,
                location=location,
                scrape=False,
                driver=self.driver,
            )
            return job
        except Exception as e:
            print(f"Error scraping job card: {e}")
            return None

    def scrape_logged_in(self, close_on_complete=True, scrape_recommended_jobs=True):
        driver = self.driver
        driver.get(self.base_url)
        if scrape_recommended_jobs:
            sleep(3)  # Wait for page to load

            # Find recommended job cards directly
            job_cards = driver.find_elements(By.CLASS_NAME, "job-card-container")
            print(f"Found {len(job_cards)} recommended jobs")

            recommended_jobs = []
            for job_card in job_cards:
                job = self.scrape_job_card(job_card)
                if job:
                    recommended_jobs.append(job)

            # Set the recommended_jobs attribute
            self.recommended_jobs = recommended_jobs
            print(f"Successfully scraped {len(recommended_jobs)} recommended jobs")

        if close_on_complete:
            driver.close()
        return
    
    def scrape_job_card_detail(self, card):
        try:
            try:
                link_elem = card.find_element(By.CLASS_NAME, "job-card-container__link")
                raw_url = link_elem.get_attribute("href")
                job_url = raw_url.split("?")[0]
                
                # Attempt to extract ID from URL or Attribute
                job_id = ""
                if "view/" in job_url:
                    job_id = job_url.split("view/")[-1].replace("/", "")
                else:
                    # Fallback: try to find data-job-id attribute often present in list items
                    job_id = card.get_attribute("data-job-id")
                
                if not job_id:
                    # Fallback: parse from query param if available
                    parsed = urllib.parse.urlparse(raw_url)
                    job_id = urllib.parse.parse_qs(parsed.query).get("currentJobId", [""])[0]

            except Exception:
                return None # Skip if no link/ID found

            # --- Extract Metadata ---
            title = link_elem.text.strip()
            
            company = "Unknown"
            try:
                company = card.find_element(By.CLASS_NAME, "artdeco-entity-lockup__subtitle").text.strip()
            except Exception as e:
                logger.error(f"Error finding element: {e}")
                pass

            # Location Parsing
            location_obj = Location(country="worldwide")
            location_text = ""
            try:
                # Try finding the metadata wrapper or specific location class
                metadata_div = card.find_element(By.CLASS_NAME, "job-card-container__metadata-wrapper")
                location_text = metadata_div.text.split("\n")[0].strip() # Often the first line
                
                parts = location_text.split(", ")
                if len(parts) == 2:
                    location_obj = Location(city=parts[0], state=parts[1], country="worldwide")
                elif len(parts) >= 3:
                    location_obj = Location(city=parts[0], state=parts[1], country=parts[2])
                else:
                    location_obj = Location(city=location_text, country="worldwide")
            except Exception as e:
                logger.error(f"Error finding element: {e}")
                pass

            # Date Posted
            date_posted = None
            try:
                time_elem = card.find_element(By.TAG_NAME, "time")
                datetime_str = time_elem.get_attribute("datetime")
                if datetime_str:
                    date_posted = datetime.strptime(datetime_str, "%Y-%m-%d")
            except Exception:
                pass

            # Remote Detection (heuristic based on text)
            is_remote = "remote" in title.lower() or "remote" in location_text.lower()

            # Construct JobPost
            job_post = JobPost(
                id=f"{job_id}",
                title=title,
                company_name=company,
                company_url="", # Hard to get without clicking
                location=location_obj,
                date_posted=date_posted,
                job_url=job_url,
                is_remote=is_remote,
                compensation=None, # Often requires clicking to see details
                description=None # Requires clicking
            )

            return job_post
            

        except Exception as e:
            return None

    def search(self, scraper_input: ScraperInput) -> JobResponse:
        """
        Searches for jobs using Selenium, mirroring the logic of the Requests-based 
        scraper for parameter building and pagination, returning JobPost objects.
        """
        job_list: JobResponse = JobResponse(jobs=[])
        seen_ids = set()
        
        # Initialize offset (LinkedIn uses 'start' parameter for pagination)
        start = scraper_input.offset // 10 * 10 if scraper_input.offset else 0
        request_count = 0
        
        # Calculate seconds for date filtering
        seconds_old = (
            scraper_input.hours_old * 3600 if scraper_input.hours_old else None
        )

        # Loop condition: continue until we have enough results or hit a safety limit
        continue_search = (
            lambda: len(job_list.jobs) < scraper_input.results_wanted and start < 1000
        )

        logger.info(f"Starting search for keywords: {scraper_input.search_term}")

        while continue_search():
            request_count += 1
            logger.info(
                f"Search page: {request_count} | Collected: {len(job_list.jobs)}/{scraper_input.results_wanted}"
            )

            # 1. Build Parameters (Exact logic from reference)
            params = {
                "keywords": scraper_input.search_term,
                "location": scraper_input.location,
                "distance": scraper_input.distance,
                "f_WT": 2 if scraper_input.is_remote else None,
                "f_JT": (
                    job_type_code(scraper_input.job_type)
                    if scraper_input.job_type
                    else None
                ),
                "start": start,
                "f_AL": "true" if scraper_input.easy_apply else None,
                "f_C": (
                    ",".join(map(str, scraper_input.linkedin_company_ids))
                    if scraper_input.linkedin_company_ids
                    else None
                ),
                "f_E": ",".join(
                    experience_level_code(x) if x else ""
                    for x in scraper_input.experience_level 
                )
            }

            # Handle Date Posted
            if seconds_old is not None:
                params["f_TPR"] = f"r{seconds_old}"

            # Remove None values
            params = {k: v for k, v in params.items() if v is not None}

            # 2. Construct URL
            query_string = urllib.parse.urlencode(params)
            search_url = os.path.join(self.base_url, "search") + f"?{query_string}"
            
            scraped_jobs_data = []
            # This set will track job IDs we've already processed
            scraped_job_ids = set()
            # 3. Navigate with Selenium
            try:
                jobs_on_page = 0
                self.driver.get(search_url)
                sleep(10)
                wait = WebDriverWait(self.driver, 15)

                # 1. Find the scrollable container
                scrollable_container_selector = (
                    By.CSS_SELECTOR, "div:has(> [data-results-list-top-scroll-sentinel])"
                )
                scrollable_container = wait.until(
                    EC.presence_of_element_located(scrollable_container_selector)
                )
                last_scroll_top = -1
                while True:
                    # Find all cards *currently* in the DOM
                    current_cards_in_dom = scrollable_container.find_elements(
                        By.CLASS_NAME, "job-card-container"
                    )                    
                    if not current_cards_in_dom:
                        logger.warning("No job cards found in container.")
                        break
                    
                    new_cards_found = 0
                    for card in current_cards_in_dom:
                        try:
                            jobs_on_page += 1
                            job_id = card.get_attribute("data-job-id")
                
                            # Check if we've already processed this job ID
                            if job_id and job_id not in scraped_job_ids:
                                scraped_job_ids.add(job_id)
                                new_cards_found += 1
                                job_post = self.scrape_job_card_detail(card)
                                if job_post:
                                    job_list.jobs.append(job_post)
                        except Exception as e:
                            logger.error(f"Error scraping a job card: {e}")           

                    self.driver.execute_script(
                        "arguments[0].scrollTop += arguments[0].clientHeight;", 
                        scrollable_container
                    )
                    
                    sleep(3) # Adjust this sleep as needed
                    
                    # 6. Check if we are at the bottom
                    current_scroll_top = self.driver.execute_script(
                        "return arguments[0].scrollTop;", scrollable_container
                    )
                    if current_scroll_top == last_scroll_top:
                        logger.info(f"Reached the end of the list. Total jobs scraped: {new_cards_found}")
                        break
                    
                    last_scroll_top = current_scroll_top
            except Exception as e:
                logger.error(f"Failed to load search URL: {e}")
                break
            
            # This will store our final results
            # 5. Pagination Logic
            if jobs_on_page == 0:
                logger.info("No unique jobs found on this page. Stopping.")
                break
                
            start += 25
            sleep(2)

        logger.info(f"Search complete. Found {len(job_list.jobs)} jobs.")
        job_list.jobs = job_list.jobs[:scraper_input.results_wanted]
        return job_list