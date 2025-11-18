import json

from job_agent.linkedin.util import extract_pdf_text
import os
import pandas as pd
import openai
import pandas as pd
from typing import List, Optional
import logging
from job_agent.linkedin.model import JobPost  # Make sure JobPost is imported
import datetime
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

    
def filter_new_companies(scraped_jobs: list[dict], existing_df: pd.DataFrame) -> list[dict]:
    if existing_df.empty:
         logger.info("Sheet is empty. All scraped jobs are new.")
         return scraped_jobs
     
    if 'company_name' not in existing_df.columns:
        logger.warning("No 'company_name' column found in the sheet. Assuming all scraped jobs are new.")
        return scraped_jobs
    existing_companies = set(existing_df['company_name'].dropna().astype(str))
    logger.info(f"Loaded {len(existing_companies)} existing job companies from the sheet.")

    new_companies = []
    for job in scraped_jobs:
        if job['company_name'] and str(job['company_name']) not in existing_companies:
            new_companies.append(job)
        elif not job['company_name']:
             logger.warning(f"Scraped job '{job['job_id']}' has no c. Skipping.")

    logger.info(f"Found {len(new_companies)} new jobs out of {len(scraped_jobs)} scraped.")
    return new_companies

# Add this new function to your JobScraperAgent class

class JobValidator:
    def __init__(self, cv_summary:str):
        self.cv_summary = cv_summary
        self.openai_client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    
    def get_job_facts(self, job_details: dict) -> Optional[dict]:
        """
        Uses GPT-4o-mini to analyze the job description and extract a rich
        JSON object of facts for later validation.
        """
        job_description_text = job_details.get("description")
        if not job_description_text:
            logging.warning(f"No description found for job {job_details.get('job_id')}, skipping AI analysis.")
            return None

        # This is the "schema" we want the AI to fill
        json_schema = {
            "is_fit": "boolean",
            "reason": "A brief 1-2 sentence explanation for the 'is_fit' decision.",
            "confidence_score": "float (0.0 to 1.0)",
            "experience_min": "integer (null if not found)",
            "experience_preferred": "integer (null if not found)",
            "required_skills": ["list", "of", "must-have", "skills"],
            "nice_to_have_skills": ["list", "of", "nice-to-have", "skills"],
            "missing_skills": ["list", "of", "required", "skills", "not", "in", "CV"],
            "skill_matching_percentage": "int (from 0 to 100, how much my skill set match with the job skill set)",
            "work_model": "string ('remote', 'hybrid', 'on-site', 'unknown')",
            "geographic_restrictions": ["list", "of", "restrictions", "e.g., 'US Only'"],
            "is_geography_valid": "boolean (do the geographic_restrictions includes Ethiopia (part of Africa like EMEA))",
            "timezone_restriction": "string (null if none, e.g., 'PST overlap required')",
            'does_hired_from_africa': "boolean (see the peoples they hired from the world and does the company previously hired from Africa)",
            'does_hired_from_ethiopia': "boolean (see the peoples they hired from the world and does the company previously hired from Ethiopia)",
            "relocation_offered": "boolean (do you know this company offering relocation offer or not)",
            "visa_sponsorship": "boolean (do you know this company offering visa sponsorship  or not)",
            "salary_min": "integer (null if not found)",
            "salary_max": "integer (null if not found)",
            "salary_currency": "string (e.g., 'USD', 'EUR', null)",
            "is_company_legit": "boolean (Is the company a known and stablished company)",
            "is_job_post_legit": "boolean (Is this job post legit and the company didn't build bad reputation of posting jobs but not hire)",
            "red_flags": ["list", "of", "red", "flags", "found", "in", "text", "any red flags you know about the company"]
        }

        system_prompt = f"""
        You are an expert HR recruitment assistant. Your task is to analyze a job description (JD)
        against a candidate's CV summary.
        Respond ONLY with a valid JSON object matching this exact structure:
        {json.dumps(json_schema, indent=4)}

        Here are the rules for your analysis:
        1.  **is_fit**: Set to 'true' ONLY IF the job is a good match based on the candidate's CV and preferences (remote-first, AI/ML roles).
        2.  **experience_min**: Find the *minimum* years required (e.g., "3+ years" -> 3, "3-5 years" -> 3).
        3.  **missing_skills**: Be strict. Compare the JD's 'required_skills' to the CV and list what is NOT in the CV.
        4.  **geographic_restrictions**: Be thorough. Find *any* mention of location. If it says "Remote" with no other text, this list should be empty. If it says "Remote (US)", add "US Only".
        5.  **red_flags**: Look for scam-like text, crypto, vague JDs, or personal email addresses.
        """
        
        user_prompt = f"""
        Candidate CV Summary:
        ---
        {self.cv_summary}
        ---
        Job Description (from Job ID {job_details.get('job_id')}):
        ---
        {job_description_text[:6000]}
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
            
            result_json_str = response.choices[0].message.content
            if result_json_str:
                return json.loads(result_json_str)
        
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse JSON response from AI: {e}\nResponse: {result_json_str}")
            return None
        except Exception as e:
            logging.error(f"Error calling OpenAI API: {e}")
            return None
    
    def validate_job(self, job_detail, validation_data:dict={
        'experience_years': 3,
        'is_remote': True,
        'max_salary': 100000,
        'work_model': 'remote',
        'max_applicants_count': 300
    }):
        # 1. validate years of experience required
        validations = {}
        
        try:
            job_data = self.get_job_facts(job_detail)
            if not job_data:
                logger.error("Failed to get job facts using llm")
                return validations
            
            validations['is_fit'] = job_data['is_fit']
            validations['confidence_score'] = job_data['confidence_score']
            validations['reason'] = job_data['reason']
            validations['is_experience_year_less_3'] =  isinstance(job_data["experience_min"], int) and job_data["experience_min"] <= validation_data["experience_years"]
            validations['job_min_experience_years'] = job_data["experience_min"]
                    
            # 2. validate the work mode
            validations['is_work_mode_valid'] = job_data['work_model'] == validation_data['work_model'] or job_data['relocation_offered']
            validations['job_work_model'] = job_data['work_model']
            validations['relocation_offered'] = job_data['relocation_offered'] or job_data['visa_sponsorship']
            
            # 3. validate the job salary range
            validations['is_salary_valid'] =  isinstance(job_data['salary_max'], int) and job_data['salary_max'] <= validation_data['max_salary']
            validations['job_max_salary'] = job_data['salary_max']
        
            # 4. validate the geographic restrictions
            validations['is_geo_valid'] = job_data["is_geography_valid"]
            validations['geographic_restrictions'] = str(job_data["geographic_restrictions"])
            
            # 5. validate the number of people applied
            validations['is_not_saturated'] = isinstance(job_detail['applicants_count'], int) and job_detail['applicants_count'] <= validation_data['max_applicants_count']
            validations['applicants_count'] = str(job_detail["applicants_count"])
            
            # 6. do have Africa working people in the company
            validations['does_hired_from_africa'] = isinstance(job_data['does_hired_from_africa'], bool) and job_data['does_hired_from_africa'] == True
            validations['does_hired_from_ethiopia'] = isinstance(job_data['does_hired_from_ethiopia'], bool) and job_data['does_hired_from_ethiopia'] == True
            validations['company_people_locations'] = job_detail['company_people_locations']
    
            # 7. validate the skill set matches
            validations['skill_matching_perc'] = job_data['skill_matching_percentage']
            validations['required_skills']=", ".join(job_data["required_skills"] + ['nice_to_have_skills: ']  + job_data['required_skills'])
            validations['missing_skills']= ", ".join(job_data['missing_skills'])
            
            # 8. Validate the company legit and job post legit
            validations['is_company_legit'] = job_data['is_company_legit']
            validations['is_job_post_legit'] = job_data['is_job_post_legit']
            validations['red_flags'] = ", ".join(job_data['red_flags'])        
            return validations
        
        except Exception as e:
            logging.error(f"Error doing validation: {e}")
            return validations
    
if __name__ == "__main__":
    job_validator = JobValidator()
    data = {'job_id': '4322974063', 'job_title': 'Ai Engineer - Remote', 'company_name': 'RemoteHunter', 'company_linkedin_url': 'https://www.linkedin.com/company/remotehunter/life/', 'posted_date': datetime.datetime(2025, 11, 17, 5, 15, 11, 143855), 'applicants_count': 32, 'description': 'About the Opportunity:\nThe organization delivers research, data science, and consulting services to help utilities make data-driven decisions that benefit customers, business outcomes, and sustainability. The Senior AI/ML Engineer will design, develop, and deploy advanced machine learning and AI solutions to support utility-focused applications, collaborating with cross-functional teams to enable scalable, intelligent systems.\n\n\nResponsibilities:\n• Collaborate with cross-functional teams to design, develop, and deploy scalable software products that incorporate machine learning and AI models.\n• Build reusable Python packages to support ML/AI algorithms and data-processing pipelines.\n• Contribute to the design of AI systems, including retrieval-augmented generation, LLM integration, and agent-based workflows.\n• Develop evaluation and monitoring frameworks to assess model reasoning, consistency, and fairness.\n• Evaluate database design and create optimized queries for efficient data processing and retrieval.\n• Break down complex MLE and AI tasks into manageable user and technical stories.\n• Ensure high-quality test coverage of ML code and participate in peer reviews.\n• Stay updated on advances in machine learning engineering, generative AI, and system orchestration.\n• Contribute to continuous delivery and Agile development processes in ML and AI engineering.\n\n\nRequirements:\n• Master’s degree in computer science, software engineering, data science, or related field (PhD preferred).\n• Minimum of 7 years of professional experience designing, developing, and deploying machine learning software products.\n• Strong programming skills in Python, including developing reusable packages and automation tools.\n• Familiarity with Databricks for scalable data processing and collaborative analytics.\n• Understanding of machine learning systems design, including model lifecycle management, MLOps, and scalable inference.\n• Hands-on experience with cloud infrastructure (Azure, AWS, or GCP), containerization, and CI/CD pipelines.\n• Proficiency with distributed computing frameworks, machine learning packages, and both relational and nonrelational databases.\n• Familiarity with generative AI frameworks (e.g., AutoGen, Hugging Face, LangChain, LangGraph, LlamaIndex) and their integration into enterprise pipelines.\n• Experience with agentic AI systems, AI orchestration, or AI-assisted decision-making workflows is an asset.\n• Excellent problem-solving and analytical skills.\n• Strong communication and collaboration abilities.\n• Knowledge or experience in the utility, power, or energy sectors is a plus.\n• Deep knowledge in Databricks tech stack for AI and data engineering is a plus.\n\n\nBenefits & Perks:\n• Medical, dental, and vision insurance options.\n• Company-paid life insurance and disability insurance.\n• Medical and dependent-care flexible spending plans.\n• Flexible time off program with manager approval.\n• Flexible schedules and work locations.\n• Paid parental leave benefit.\n• 401(k)/RRSP plan with a 3% employer match.\n\n\nCompensation:\n• $115,000–$130,000 USD salary per year plus annual bonus.\n• Actual pay will be adjusted based on experience.\n• 100% remote role with infrequent travel (generally 1–2 times per year).\n\n\nNote:\nRemoteHunter is not the Employer of Record (EOR) for this role. Our purpose in this opportunity is to connect exceptional candidates with leading employers. We help job seekers worldwide discover roles that match their goals and guide them to complete their full application directly through the hiring company’s career page or ATS.\n…\n more', 'url': 'https://www.linkedin.com/jobs/view/4322974063/', 'apply_type': 'External Apply', 'job_application_url': 'https://www.remotehunter.com/apply-with-ai/7cbcd4d9-aaaf-4eb4-8abd-d4d5feb78e59?utm_medium=job_posting&utm_source=linkedin&utm_category=software_engineer&utm_campaign=software_engineer_remote&utm_term=ai_engineer_remote'}  
    validations = job_validator.validate_job(data)