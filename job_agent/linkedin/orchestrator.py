import logging
import pandas as pd
from job_agent.linkedin.job_validator import JobValidator, filter_new_companies, filter_new_jobs
from job_agent.linkedin.main import JobScraperAgent
from job_agent.linkedin.model import Country, ScraperInput, Site, ExperienceLevel
from job_agent.linkedin.sheet_manager import GoogleSheetManager

# --- Configuration ---
SHEET_FILE_NAME = "My AI Job Tracker"
TAB_ALL_JOBS = "All Jobs"
TAB_COMPANIES = "Companies"
TAB_VALIDATED = "Validated Jobs"

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def safe_read_sheet(manager: GoogleSheetManager, tab_name: str) -> pd.DataFrame:
    """Safely reads a sheet into a DataFrame, returning an empty DF on failure."""
    try:
        read_data = manager.read_sheet(tab_name)
        if not read_data:
            return pd.DataFrame()
        return pd.DataFrame(read_data)
    except Exception as e:
        logger.warning(f"Could not read sheet '{tab_name}' (might be empty or missing): {e}")
        return pd.DataFrame()

def fetch_job_details_safely(agent, new_jobs_to_process):
    """Iterates through jobs and fetches details, skipping failures."""
    job_details_list = []
    
    logger.info(f"Attempting to fetch details for {len(new_jobs_to_process)} jobs...")
    
    for job in new_jobs_to_process:
        try:
            # Fetch details
            job_detail = agent.get_job_details_by_id(job.id)
            
            # Basic enrichment
            if job.location:
                job_detail['job_location_scraped_from_linkedin'] = (
                    f"country: {job.location.country}, city: {job.location.city}, state: {job.location.state}"
                )
            
            # Filter: Skip Easy Apply
            if job_detail.get('apply_type') != "Easy Apply":
                job_details_list.append(job_detail)
            else:
                logger.info(f"Skipping Job ID {job.id}: 'Easy Apply' detected.")

        except Exception as e:
            logger.error(f"Failed to process Job ID {job.id}: {e}", exc_info=False)
            continue  # CRITICAL: Move to next job on failure

    return job_details_list

def update_company_stats(agent, manager, job_details_list, companies_df):
    """
    Identifies new companies, scrapes their stats, updates the sheet, 
    and returns a mapper for job enrichment.
    """
    new_companies_to_process = filter_new_companies(job_details_list, companies_df)
    
    companies_data_rows = []
    new_companies_list_dicts = []
    
    logger.info(f"Found {len(new_companies_to_process)} new companies to analyze.")

    for company in new_companies_to_process:
        comp_url = company.get('company_linkedin_url')
        comp_name = company.get('company_name')
        
        try:
            if not comp_url:
                raise ValueError("Missing Company URL")

            company_people_locations = agent.scrape_company_location_stats(comp_url)
            loc_str = str(company_people_locations)
            
            companies_data_rows.append([comp_name, comp_url, loc_str])
            new_companies_list_dicts.append({
                "company_name": comp_name, 
                "company_linkedin_url": comp_url, 
                "company_people_locations": loc_str
            })
            
        except Exception as e:
            logger.error(f"Failed to scrape stats for company '{comp_name}': {e}")
            # We create a placeholder so the job processing doesn't fail later
            new_companies_list_dicts.append({
                "company_name": comp_name, 
                "company_linkedin_url": comp_url, 
                "company_people_locations": "{}"
            })
            continue

    # 1. Update Google Sheet with new companies
    if companies_data_rows:
        try:
            manager.append_rows(
                tab_name=TAB_COMPANIES, 
                headers=["company_name", "company_linkedin_url", "company_people_locations"], 
                rows=companies_data_rows
            )
            logger.info(f"Saved {len(companies_data_rows)} new companies to sheet.")
        except Exception as e:
            logger.error(f"Failed to save companies to sheet: {e}")

    # 2. Merge old and new data to create the mapper
    all_companies_df = pd.concat([companies_df, pd.DataFrame(new_companies_list_dicts)], ignore_index=True)
    companies_dict = all_companies_df.to_dict(orient='records')
    
    # Create Mapper
    companies_loc_mapper = {}
    for comp in companies_dict:
        # FIX: ensure we handle missing keys safely
        c_name = comp.get('company_name')
        if c_name:
            companies_loc_mapper[c_name] = comp.get('company_people_locations', "{}")
            
    return companies_loc_mapper

def validate_and_prepare_jobs(job_details_list, companies_loc_mapper, cv_summary):
    """Runs the validation logic on every job safely."""
    validator = JobValidator(cv_summary)
    jobs_to_save = []
    
    # Column headers definition
    jobs_col = [
        "job_title", "company_name", "job_application_url", 'url', "is_fit", 
        'confidence_score', 'skill_matching_perc', 'is_experience_year_less_3', 
        'is_work_mode_valid', 'is_salary_valid', 'is_geo_valid', 'is_not_saturated',
        'does_hired_from_africa', 'does_hired_from_ethiopia', 'is_company_legit', 
        'is_job_post_legit', 'reason', 'job_min_experience_years',
        'job_work_model', 'relocation_offered', 'job_max_salary', 'geographic_restrictions', 
        'applicants_count', 'company_people_locations', 'required_skills', 'missing_skills', 'red_flags'
    ]

    for job in job_details_list:
        try:
            # Enrich with company location data
            comp_name = job.get('company_name')
            job['company_people_locations'] = companies_loc_mapper.get(comp_name, "{}")

            # Run Validation
            validations = validator.validate_job(job)
            
            # Prepare row
            jobs_to_save.append([
                job.get("job_title"), job.get('company_name'), job.get('job_application_url'), job.get('url'),
                validations.get('is_fit'), validations.get('confidence_score'), validations.get('skill_matching_perc'),
                validations.get('is_experience_year_less_3'), validations.get('is_work_mode_valid'),
                validations.get('is_salary_valid'), validations.get('is_geo_valid'), validations.get('is_not_saturated'),
                validations.get('does_hired_from_africa'), validations.get('does_hired_from_ethiopia'), 
                validations.get('is_company_legit'), validations.get('is_job_post_legit'), validations.get('reason'), 
                validations.get('job_min_experience_years'), validations.get('job_work_model'), 
                validations.get('relocation_offered'), validations.get('job_max_salary'),
                validations.get('geographic_restrictions'), validations.get('applicants_count'), 
                validations.get('company_people_locations'), validations.get('required_skills'), 
                validations.get('missing_skills'), validations.get('red_flags')
            ])
        except Exception as e:
            logger.error(f"Error validating job '{job.get('job_title', 'Unknown')}': {e}")
            continue

    return jobs_col, jobs_to_save

def main():
    agent = JobScraperAgent()
    manager = GoogleSheetManager(SHEET_FILE_NAME)

    # 1. Search Phase
    keywords = ['AI Engineer', "Generative AI Engineer", "AI Agent Engineer", "Python Developer", "Software Engineer"]
    all_job_ids = set()
    found_job_objects = []

    logger.info("Starting Job Search...")
    for search_term in keywords:
        try:
            scrape_input = ScraperInput(
                site_type=[Site.LINKEDIN],
                search_term=search_term,
                country=Country.WORLDWIDE,
                location='worldwide',
                is_remote=True,
                easy_apply=False,
                hours_old=24,
                results_wanted=5,
                experience_level=[ExperienceLevel.ENTRY_LEVEL, ExperienceLevel.ASSOCIATE, ExperienceLevel.MID_SENIOR_LEVEL]
            )
            
            jobs = agent.find_jobs(scrape_input)
            
            count = 0
            for job in jobs.jobs:
                if job.id not in all_job_ids:
                    all_job_ids.add(job.id)
                    found_job_objects.append(job)
                    count += 1
            logger.info(f"Found {count} new unique jobs for term: {search_term}")
            
        except Exception as e:
            logger.error(f"Search failed for keyword '{search_term}': {e}")
            continue

    # 2. Read Existing Data & Filter
    df_existing_jobs = safe_read_sheet(manager, TAB_ALL_JOBS)
    new_jobs_to_process = filter_new_jobs(found_job_objects, df_existing_jobs)

    if not new_jobs_to_process:
        logger.info("No new jobs to process after filtering. Exiting.")
        return

    # 3. Fetch Details (Resilient Loop)
    job_details_list = fetch_job_details_safely(agent, new_jobs_to_process)

    if not job_details_list:
        logger.warning("No job details could be successfully fetched. Exiting.")
        return

    # 4. Company Stats Processing
    companies_df = safe_read_sheet(manager, TAB_COMPANIES)
    companies_loc_mapper = update_company_stats(agent, manager, job_details_list, companies_df)

    # 5. Validation & Preparation
    cv_summary = manager.extract_text_from_drive_pdf("resume.pdf", is_file_id=False)
    headers, rows_to_save = validate_and_prepare_jobs(job_details_list, companies_loc_mapper, cv_summary)

    # 6. Save Validated Jobs
    if rows_to_save:
        try:
            manager.append_rows(tab_name=TAB_VALIDATED, headers=headers, rows=rows_to_save)
            logger.info(f"Successfully saved {len(rows_to_save)} validated jobs to '{TAB_VALIDATED}'.")
        except Exception as e:
            logger.error(f"Failed to save validated jobs: {e}")
    else:
        logger.info("No jobs passed validation or processing.")

    # 7. Update 'All Jobs' Log (Last step to ensure we tracked what we processed)
    jobs_log_rows = []
    for job in new_jobs_to_process:
        jobs_log_rows.append([job.id, job.title, job.company_name, job.job_url])
    
    if jobs_log_rows:
        try:
            manager.append_rows(tab_name=TAB_ALL_JOBS, headers=["id", "title", "company_name", "job_url"], rows=jobs_log_rows)
            logger.info("Updated 'All Jobs' tracker sheet.")
        except Exception as e:
            logger.error(f"Failed to update 'All Jobs' sheet: {e}")

if __name__ == "__main__":
    main()
