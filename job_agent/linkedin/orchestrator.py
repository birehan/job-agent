import json

from job_agent.linkedin.job_validator import filter_new_companies, filter_new_jobs
from job_agent.linkedin.util import extract_pdf_text
from .main import JobScraperAgent
from .model import Country, JobType, ScraperInput, Site, JobResponse, ExperienceLevel
from .sheet_manager import GoogleSheetManager
import os
import pandas as pd

import pandas as pd
from typing import List, Optional
import logging
from .model import JobPost  # Make sure JobPost is imported

# Get a logger for this module
logger = logging.getLogger(__name__)
    

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
            hours_old=48,
            results_wanted=5,
            experience_level=[ExperienceLevel.ENTRY_LEVEL, ExperienceLevel.ASSOCIATE, ExperienceLevel.MID_SENIOR_LEVEL]
        )
        
        jobs = agent.find_jobs(scrape_input)
        for job in jobs.jobs:
            if job.id not in all_job_ids:
                all_job_ids.add(job.id)
                all_jobs.append(job)
    
    SHEET_FILE_NAME = "My AI Job Tracker"
    all_jobs_tab = "All Jobs"    
    companies_tab = "Companies"
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

    job_details =[]
    try:
        read_data = manager.read_sheet(companies_tab)
        companies_df = pd.DataFrame(read_data)
    except Exception as e:
        logger.error(f"Failed to read sheet: {e}. Starting with an empty DataFrame.")
        companies_df = pd.DataFrame()
    
    
    # Ge each job details
    for job in new_jobs_to_process:
        job_detail = agent.get_job_details_by_id(job.id)        # Validate to get only not easy apply jobs
        if job_detail['apply_type'] != "Easy Apply":
            job_details.append(job_detail)
    
    new_companies_to_process = filter_new_companies(job_details, companies_df)
    companies_data = []
    for company in new_companies_to_process:
        company_people_locations = agent.scrape_company_location_stats(company['company_linkedin_url'])
        companies_data.append([company['company_name'], company['company_linkedin_url'], company_people_locations])
    
    manager.append_rows(tab_name=companies_tab, headers=["company_name", "company_linkedin_url", "company_people_locations"], rows=companies_data)
    breakpoint()
    
    # TODO Validate the job post is legit and the company is legit company?
    # TODO is hire from africa ? validate they hire from Afirca and Ethiopia
    # TODO do have relocation ?
    # TODO does hire remotely ?
    # TODO validate the job experience years requirement 3 years
    # TODO Validate the job requirement skills match with my resume

    
     # add it to all jobs sheet
    jobs_to_add = []
    for job in new_jobs_to_process: 
        jobs_to_add.append([job.id, job.title, job.company_name,  job.job_url])    
    manager.append_rows(tab_name=all_jobs_tab, headers=["id", "title", "company_name", "job_url"], rows=jobs_to_add)
    breakpoint()
    
    # get the job detail
    
    
if __name__ == "__main__":
    main()