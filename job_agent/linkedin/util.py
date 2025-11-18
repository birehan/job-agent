
import logging
from .model import JobType, ExperienceLevel
import os
import PyPDF2

# Get a logger for this module
logger = logging.getLogger(__name__)


def extract_pdf_text(pdf_path: str) -> str:
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
    
def job_type_code(job_type_enum: JobType) -> str:
    return {
        JobType.FULL_TIME: "F",
        JobType.PART_TIME: "P",
        JobType.INTERNSHIP: "I",
        JobType.CONTRACT: "C",
        JobType.TEMPORARY: "T",
    }.get(job_type_enum, "")


def experience_level_code(experience_level_enum: ExperienceLevel) -> str:
    return {
        ExperienceLevel.INTERNSHIP: "1",
        ExperienceLevel.ENTRY_LEVEL: "2",
        ExperienceLevel.ASSOCIATE: "3",
        ExperienceLevel.MID_SENIOR_LEVEL: "4",
        ExperienceLevel.DIRECTOR: "5",
        ExperienceLevel.EXECUTIVE: "6",
        
    }.get(experience_level_enum, "")
    
def create_logger(name: str):
    logger = logging.getLogger(f"{name}")
    logger.propagate = False
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        console_handler = logging.StreamHandler()
        format = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
        formatter = logging.Formatter(format)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    return logger

