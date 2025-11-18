import gspread
import logging
import os
from google.oauth2 import service_account
from typing import List, Dict, Any, Optional, Tuple
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import pypdf
import io
# Set up a logger for this module
logger = logging.getLogger(__name__)

class GoogleSheetManager:
    """
    A dedicated class to handle all Google Sheets API interactions,
    including reading, writing, and managing tabs (Worksheets).
    """

    def __init__(self, spreadsheet_name: str):
        """
        Initializes the manager and authenticates with Google.
        
        Args:
            spreadsheet_name (str): The exact name of the Google Sheet to open.
        """
        try:
            logger.info(f"Authenticating with Google Sheets...")
            self.client, self.credentials = self._authenticate()
            
            logger.info(f"Opening spreadsheet: {spreadsheet_name}...")
            self.spreadsheet = self.client.open(spreadsheet_name)
            
            self.drive_service = build('drive', 'v3', credentials=self.credentials)
            
            logger.info("Successfully connected to Google Spreadsheet.")
        except gspread.exceptions.SpreadsheetNotFound:
            logger.error(f"Spreadsheet '{spreadsheet_name}' not found.")
            logger.error("Please create it and share it with the service account email.")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize GoogleSheetManager: {e}")
            raise

    def _authenticate(self) -> Tuple[gspread.Client, service_account.Credentials]:
        """
        Handles the authentication flow using environment variables.
        (This logic is moved directly from your JobScraperAgent)
        """
        service_account_info = {
            "project_id": os.environ.get("GOOGLE_CLOUD_PROJECT_ID", ""),
            "private_key": os.environ.get("GOOGLE_CLOUD_PRIVATE_KEY", ""),
            "client_email": os.environ.get("GOOGLE_CLOUD_CLIENT_EMAIL", ""),
            "token_uri": "https://oauth2.googleapis.com/token",
            "type": "service_account", # Added for robustness
        }
        
        if not all([service_account_info["project_id"], 
                    service_account_info["private_key"], 
                    service_account_info["client_email"]]):
            raise ValueError("Missing Google Cloud service account environment variables.")

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        
        creds = service_account.Credentials.from_service_account_info(
            service_account_info, scopes=scopes
        )
        return gspread.authorize(creds), creds

    
    def get_file_id_by_name(self, filename: str) -> Optional[str]:
        """
        Helper to find a file ID given its name. 
        Returns the ID of the first match found.
        """
        try:
            # Query to find file by name, ensuring it's not in the trash
            query = f"name = '{filename}' and trashed = false"
            results = self.drive_service.files().list(
                q=query, pageSize=1, fields="files(id, name)"
            ).execute()
            items = results.get('files', [])
            
            if not items:
                logger.warning(f"No file found with name '{filename}'")
                return None
            
            return items[0]['id']
        except Exception as e:
            logger.error(f"Error searching for file '{filename}': {e}")
            return None

    def extract_text_from_drive_pdf(self, file_identifier: str, is_file_id: bool = False) -> str:
        """
        Downloads a PDF from Google Drive and extracts its text.

        Args:
            file_identifier (str): The File ID (e.g., '1A2b3C...') or the File Name (e.g., 'resume.pdf').
            is_file_id (bool): Set to True if passing an ID, False if passing a filename.

        Returns:
            str: The extracted text content.
        """
        file_id = file_identifier

        # If a name was passed, resolve it to an ID first
        if not is_file_id:
            logger.info(f"Looking up ID for filename: {file_identifier}")
            found_id = self.get_file_id_by_name(file_identifier)
            if not found_id:
                return ""
            file_id = found_id

        try:
            logger.info(f"Downloading file ID: {file_id}...")
            request = self.drive_service.files().get_media(fileId=file_id)
            
            # Create an in-memory buffer
            file_stream = io.BytesIO()
            downloader = MediaIoBaseDownload(file_stream, request)
            
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                # logger.info(f"Download {int(status.progress() * 100)}%.")

            # Reset stream position to the beginning for reading
            file_stream.seek(0)

            logger.info("Extracting text from PDF...")
            reader = pypdf.PdfReader(file_stream)
            full_text = []
            
            for page in reader.pages:
                full_text.append(page.extract_text())
            
            return "\n".join(full_text)

        except Exception as e:
            logger.error(f"Failed to extract text from Drive file: {e}")
            return ""
        
    def _get_or_create_worksheet(self, tab_name: str) -> gspread.Worksheet:
        """
        Gets a worksheet by its tab name. If it doesn't exist, creates it.
        """
        try:
            # Try to get the worksheet
            return self.spreadsheet.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            # Create it if it doesn't exist
            logger.info(f"Worksheet '{tab_name}' not found. Creating it...")
            return self.spreadsheet.add_worksheet(title=tab_name, rows=100, cols=20)

    def append_rows(self, tab_name: str, rows: List[List[Any]], headers: Optional[List[str]] = None):
        """
        Appends one or more rows to a specific tab.
        
        If headers are provided, it will check if the sheet is empty 
        and add them before appending the data.

        Args:
            tab_name (str): The name of the tab to write to.
            rows (List[List[Any]]): A list of rows to append. 
                                    Example: [["val1", "val2"], ["valA", "valB"]]
            headers (Optional[List[str]]): A list of header strings.
        """
        if not rows and not headers:
            logger.warning(f"No data or headers provided for tab '{tab_name}'. Nothing to do.")
            return

        try:
            worksheet = self._get_or_create_worksheet(tab_name)
            
            # Check for headers if provided
            if headers:
                first_cell = worksheet.acell('A1').value
                if not first_cell:
                    logger.info(f"Setting headers for new tab '{tab_name}'...")
                    # worksheet.update('A1', [headers], value_input_option='USER_ENTERED')
                    worksheet.update(range_name='A1', values=[headers], value_input_option='USER_ENTERED')
            
            # Append the data rows
            if rows:
                worksheet.append_rows(rows, value_input_option='USER_ENTERED')
                logger.info(f"Appended {len(rows)} rows to tab '{tab_name}'.")

        except Exception as e:
            logger.error(f"Failed to append rows to tab '{tab_name}': {e}")
            
    def read_sheet(self, tab_name: str) -> List[Dict[str, Any]]:
        """
        Reads all data from a specific tab and returns it as a list of dictionaries.
        Assumes the first row of the tab is the header.
        
        Args:
            tab_name (str): The name of the tab to read from.

        Returns:
            List[Dict[str, Any]]: A list of all rows as dictionaries.
                                  Returns an empty list if the tab is not found.
        """
        try:
            worksheet = self.spreadsheet.worksheet(tab_name)
            logger.info(f"Reading all data from tab '{tab_name}'...")
            return worksheet.get_all_records()
        except gspread.exceptions.WorksheetNotFound:
            logger.error(f"Cannot read: Worksheet '{tab_name}' not found.")
            return []
        except Exception as e:
            logger.error(f"Failed to read from tab '{tab_name}': {e}")
            return []

# --- Example Usage ---
if __name__ == "__main__":
    # 1. Set up logging to see the output
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # The name of the Google Sheet file
    SHEET_FILE_NAME = "My AI Job Tracker" # Use your sheet name
    
    try:
        # 3. Initialize the manager
        manager = GoogleSheetManager(SHEET_FILE_NAME)
        pdf_text = manager.extract_text_from_drive_pdf("resume.pdf", is_file_id=False)
        # 4. Define headers and data for a 'Jobs' tab
        jobs_tab = "AI Jobs"
        job_headers = ["Timestamp", "Job Title", "Company", "Job URL"]
        job_data = [
            ["2025-11-15 10:00:00", "AI Engineer", "TechCorp", "http://example.com/1"],
            ["2025-11-15 10:01:00", "ML Specialist", "DataDriven", "http://example.com/2"]
        ]
        
        # 5. Save data (this will create the tab and add headers)
        manager.append_rows(jobs_tab, job_data, headers=job_headers)

        # 6. Define data for another tab
        applied_tab = "Applied Jobs"
        applied_headers = ["Date Applied", "Job Title", "Status"]
        applied_data = [
            ["2025-11-15", "AI Engineer", "Pending"]
        ]
        
        # 7. Save to the second tab
        manager.append_rows(applied_tab, applied_data, headers=applied_headers)

        # 8. Read data from the first tab
        print("\n--- Reading from 'AI Jobs' ---")
        read_data = manager.read_sheet(jobs_tab)
        for row in read_data:
            print(row)
        
        # 9. Read data from the second tab
        print("\n--- Reading from 'Applied Jobs' ---")
        applied_read_data = manager.read_sheet(applied_tab)
        print(applied_read_data)

    except Exception as e:
        logger.error(f"Demo failed: {e}. Did you set your environment variables?")