from .main import JobScraperAgent
from .model import Country, JobType, ScraperInput, Site, JobResponse, ExperienceLevel
from .sheet_manager import GoogleSheetManager
import os
import pandas as pd

import pandas as pd
from typing import List
import logging
from .model import JobPost  # Make sure JobPost is imported

# Get a logger for this module
logger = logging.getLogger(__name__)

def filter_new_jobs(scraped_jobs: List[JobPost], existing_df: pd.DataFrame) -> List[JobPost]:
    if existing_df.empty:
         logger.info("Sheet is empty. All scraped jobs are new.")
         return scraped_jobs
     
    if 'id' not in existing_df.columns:
        logger.warning("No 'id' column found in the sheet. Assuming all scraped jobs are new.")
        return scraped_jobs
    existing_ids = set(existing_df['id'].dropna().astype(str))
    logger.info(f"Loaded {len(existing_ids)} existing job IDs from the sheet.")

    new_jobs = []
    for job in scraped_jobs:
        if job.id and str(job.id) not in existing_ids:
            new_jobs.append(job)
        elif not job.id:
             logger.warning(f"Scraped job '{job.title}' has no ID. Skipping.")

    logger.info(f"Found {len(new_jobs)} new jobs out of {len(scraped_jobs)} scraped.")
    return new_jobs

def main():
    agent = JobScraperAgent(
    )
    keywords = ['AI Engineer', "Generative AI Engineer", "AI Agent Engineer", "Python Developer", "Software Engineer", "Backend Engineer", "Machine Learning Engineer"]
    keywords = ['AI Engineer']
    all_jobs = []
    all_job_ids = set()
    for search_term in keywords:
        scrape_input = ScraperInput(
            site_type=[Site.LINKEDIN],
            search_term=search_term,
            country = Country.WORLDWIDE,
            location='worldwide',
            is_remote=True,
            easy_apply=False,
            hours_old=24,
            results_wanted=10,
            experience_level=[ExperienceLevel.ENTRY_LEVEL, ExperienceLevel.ASSOCIATE, ExperienceLevel.MID_SENIOR_LEVEL]
        )
        
        jobs = agent.find_jobs(scrape_input)
        for job in jobs.jobs:
            if job.id not in all_job_ids:
                all_job_ids.add(job.id)
                all_jobs.append(job)
    
    SHEET_FILE_NAME = "My AI Job Tracker"
    all_jobs_tab = "All Jobs"    
    manager = GoogleSheetManager(SHEET_FILE_NAME)
    try:
        read_data = manager.read_sheet(all_jobs_tab)
        df = pd.DataFrame(read_data)
    except Exception as e:
        logger.error(f"Failed to read sheet: {e}. Starting with an empty DataFrame.")
        df = pd.DataFrame()

    new_jobs_to_process = filter_new_jobs(jobs.jobs, df)    
    if not new_jobs_to_process:
        logger.info("No new jobs to process. Exiting.")
        
    # Ge each job details
    for job in new_jobs_to_process:
        job_detail = agent.get_job_details_by_id(job.id)    
    
     # add it to all jobs sheet
    jobs_to_add = []
    for job in new_jobs_to_process: 
        jobs_to_add.append([job.id, job.title, job.company_name,  job.job_url])    
    manager.append_rows(tab_name=all_jobs_tab, headers=["id", "title", "company_name", "job_url"], rows=jobs_to_add)
    breakpoint()
    
    # get the job detail
    
    
if __name__ == "__main__":
    main()