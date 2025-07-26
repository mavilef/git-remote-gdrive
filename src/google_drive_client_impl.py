import os.path
import tempfile
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from io import BytesIO
from googleapiclient.http import MediaIoBaseDownload

from .google_drive_client import GoogleDriveClient

# If modifying these scopes, delete the file token.pickle.
SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly", "https://www.googleapis.com/auth/drive.readonly"]

class GoogleDriveClientImpl(GoogleDriveClient):
    def __init__(self, credentials_path: str, token_path: str = None):
        """Initializes the GoogleDriveClientImpl.

        Args:
            credentials_path: The path to the Google API client secrets JSON file.
            token_path: Optional; The path to store the OAuth token. Defaults to a temporary file.
        """
        self.credentials_path = credentials_path
        if token_path is None:
            self.token_path = os.path.join(tempfile.gettempdir(), "token.pickle")
        else:
            self.token_path = token_path
        
        creds = self._authenticate()
        self.service = build("drive", "v3", credentials=creds)

    def _authenticate(self) -> Credentials:
        """Handles Google API authentication flow.

        It attempts to load credentials from a token file. If not found or expired,
        it initiates an OAuth 2.0 flow to obtain new credentials and saves them.

        Returns:
            A Google API Credentials object.
        """
        creds = None

        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(self.token_path, "w") as token:
                token.write(creds.to_json())
        
        return creds

    def list_files(self, folder_id: str) -> list[str]:
        """Lists files within a specified Google Drive folder.

        Args:
            folder_id: The ID of the Google Drive folder.

        Returns:
            A list of file names within the folder.
        """
        try:
            files = []
            page_token = None
            while True:
                query = f"'{folder_id}' in parents and trashed = false"
                results = self.service.files().list(
                    q=query,
                    fields="nextPageToken, files(name)",
                    pageToken=page_token
                ).execute()
                items = results.get("files", [])
                files.extend([item["name"] for item in items])
                page_token = results.get("nextPageToken", None)
                if page_token is None:
                    break
            return files
        except HttpError as error:
            print(f"An error occurred: {error}")
            return []

    def read_file_content(self, file_path: str) -> bytes:
        """Reads the content of a file from Google Drive.

        Args:
            file_path: The path to the file on Google Drive.

        Returns:
            The content of the file as bytes.
        """
        try:
            # First, find the file ID by its path (name in this context)
            # This assumes file_path is the name of the file within the current folder_id context
            # For a full path, we'd need to traverse folders or search more broadly.
            # For now, let's assume file_path is the 'name' of the file we're looking for.
            # We need to get the file ID first.
            query = f"name = '{file_path}' and trashed = false"
            results = self.service.files().list(
                q=query,
                fields="files(id)"
            ).execute()
            items = results.get("files", [])

            if not items:
                print(f"File not found: {file_path}")
                return b""
            
            file_id = items[0]["id"]

            request = self.service.files().get_media(fileId=file_id)
            fh = BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                # print(f"Download {int(status.progress() * 100)}%.")
            return fh.getvalue()
        except HttpError as error:
            print(f"An error occurred: {error}")
            return b""

if __name__ == "__main__":
    credentials_path = os.environ.get("GDRIVE_CREDENTIALS_PATH")

    if not credentials_path:
        print("Error: GDRIVE_CREDENTIALS_PATH environment variable not set.")
        print("Please set GDRIVE_CREDENTIALS_PATH to the path of your credentials.json file.")
        sys.exit(1)

    test_folder_id = "root" 
    if len(sys.argv) > 1:
        test_folder_id = sys.argv[1]

    try:
        client = GoogleDriveClientImpl(credentials_path)
        print(f"Listing files in folder ID: {test_folder_id}")
        files = client.list_files(test_folder_id)
        if files:
            print("Files found:")
            for f in files:
                print(f"- {f}")
            
            # Example of reading content of the first file found
            if files:
                first_file_name = files[0]
                print(f"\nReading content of '{first_file_name}':")
                content = client.read_file_content(first_file_name)
                print(f"Content length: {len(content)} bytes")
                print(content.decode('utf-8')) # Uncomment to print content if it's text
        else:
            print("No files found.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")