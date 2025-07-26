import os.path
import tempfile
import sys
import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from io import BytesIO
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from .google_drive_client import GoogleDriveClient

logger = logging.getLogger(__name__)

# If modifying these scopes, delete the file token.pickle.
SCOPES = ["https://www.googleapis.com/auth/drive"]

class GoogleDriveClientImpl(GoogleDriveClient):
    def __init__(self, credentials_path: str, token_path: str = None):
        logger.info(f"GoogleDriveClientImpl __init__ called with credentials_path={credentials_path}, token_path={token_path}")
        self.credentials_path = credentials_path
        if token_path is None:
            self.token_path = os.path.join(tempfile.gettempdir(), "token.pickle")
        else:
            self.token_path = token_path
        
        creds = self._authenticate()
        self.service = build("drive", "v3", credentials=creds)
        logger.info("Google Drive service built.")

    def _authenticate(self) -> Credentials:
        logger.info("Authenticating with Google Drive...")
        creds = None

        if os.path.exists(self.token_path):
            logger.info(f"Token file found at {self.token_path}.")
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired token.")
                creds.refresh(Request())
            else:
                logger.info("No valid token found, initiating new OAuth flow.")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(self.token_path, "w") as token:
                logger.info(f"Saving token to {self.token_path}.")
                token.write(creds.to_json())
        logger.info("Authentication successful.")
        return creds

    def _get_or_create_path_id(self, path: str, root_folder_id: str) -> str | None:
        """Resolves a path to a folder ID, creating intermediate folders if necessary.

        Args:
            path: The path to resolve (e.g., 'refs/heads/main' or 'refs/heads').
            root_folder_id: The ID of the root folder to start the path resolution from.

        Returns:
            The ID of the deepest folder in the path, or None if an error occurs.
        """
        current_parent_id = root_folder_id
        path_parts = path.split('/')

        # Determine if the last part is a file or a folder
        is_file = '.' in path_parts[-1] or path.startswith("refs/") and len(path_parts) > 1 and not path_parts[-1].startswith("refs/")
        
        # Iterate through path parts, excluding the last one if it's a file
        parts_to_process = path_parts[:-1] if is_file else path_parts

        for part in parts_to_process:
            folder_id = self.get_file_id_by_name(part, current_parent_id)
            if folder_id is None:
                logger.info(f"Folder '{part}' not found, creating it under {current_parent_id}.")
                folder_id = self.create_folder(part, current_parent_id)
                if not folder_id:
                    logger.error(f"Failed to create folder: {part}")
                    return None # Failed to create folder
                logger.info(f"Folder '{part}' created with ID: {folder_id}")
            else:
                logger.info(f"Folder '{part}' found with ID: {folder_id}")
            current_parent_id = folder_id
        
        logger.info(f"Resolved path '{path}' to parent folder ID: {current_parent_id}")
        return current_parent_id

    def list_files(self, folder_path: str, root_folder_id: str) -> list[str]:
        """Lists files within a specified Google Drive folder path.

        Args:
            folder_path: The path to the folder (e.g., 'refs/heads').
            root_folder_id: The ID of the root folder to start the path resolution from.

        Returns:
            A list of file names within the folder.
        """
        try:
            target_folder_id = self._get_or_create_path_id(folder_path, root_folder_id)
            if not target_folder_id:
                return []

            files = []
            page_token = None
            while True:
                query = f"'{target_folder_id}' in parents and trashed = false"
                logger.info(f"Listing files with query: {query}, pageToken: {page_token}")
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
            logger.info(f"Found {len(files)} files in {folder_path}.")
            return files
        except HttpError as error:
            logger.error(f"An HttpError occurred during list_files: {error}")
            return []

    def read_file_content(self, file_path: str, root_folder_id: str) -> bytes:
        """Reads the content of a file from Google Drive.

        Args:
            file_path: The full path to the file on Google Drive (e.g., 'refs/heads/main').
            root_folder_id: The ID of the root folder to start the path resolution from.

        Returns:
            The content of the file as bytes.
        """
        try:
            parent_folder_id = self._get_or_create_path_id(file_path, root_folder_id)
            if not parent_folder_id:
                logger.warning(f"Could not resolve parent folder for reading: {file_path}")
                return b""

            file_name = os.path.basename(file_path)
            file_id = self.get_file_id_by_name(file_name, parent_folder_id)

            if not file_id:
                logger.warning(f"File not found: {file_path}")
                return b""
            
            logger.info(f"Downloading content for file ID: {file_id}")
            request = self.service.files().get_media(fileId=file_id)
            fh = BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                # logger.debug(f"Download {int(status.progress() * 100)}%.")
            logger.info(f"Content downloaded for {file_path}. Size: {len(fh.getvalue())} bytes.")
            return fh.getvalue()
        except HttpError as error:
            logger.error(f"An HttpError occurred during read_file_content: {error}")
            return b""

    def get_file_id_by_name(self, name: str, parent_id: str) -> str | None:
        """Gets the ID of a file or folder by its name within a specific parent folder.

        Args:
            name: The name of the file or folder.
            parent_id: The ID of the parent folder to search within.

        Returns:
            The ID of the file/folder if found, otherwise None.
        """
        try:
            query = f"name = '{name}' and '{parent_id}' in parents and trashed = false"
            results = self.service.files().list(
                q=query,
                fields="files(id)"
            ).execute()
            items = results.get("files", [])
            if items:
                logger.info(f"Found file/folder '{name}' with ID: {items[0]["id"]}")
                return items[0]["id"]
            logger.info(f"File/folder '{name}' not found under parent {parent_id}.")
            return None
        except HttpError as error:
            logger.error(f"An HttpError occurred during get_file_id_by_name: {error}")
            return None

    def create_folder(self, name: str, parent_id: str) -> str:
        """Creates a new folder on Google Drive.

        Args:
            name: The name of the new folder.
            parent_id: The ID of the parent folder where the new folder should be created.

        Returns:
            The ID of the newly created folder.
        """
        try:
            file_metadata = {
                'name': name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id]
            }
            file = self.service.files().create(body=file_metadata, fields='id').execute()
            logger.info(f"Folder '{name}' created with ID: {file.get('id')}")
            return file.get('id')
        except HttpError as error:
            logger.error(f"An HttpError occurred during create_folder: {error}")
            return ""

    def upload_file(self, name: str, content: bytes, parent_path: str, root_folder_id: str) -> str:
        """Uploads a file to Google Drive or updates an existing one.

        If a file with the given name already exists in the parent folder,
        its content will be updated. Otherwise, a new file will be created.

        Args:
            name: The name of the file.
            content: The content of the file as bytes.
            parent_path: The path to the parent folder where the file should be (e.g., 'refs/heads').
            root_folder_id: The ID of the root folder to start the path resolution from.

        Returns:
            The ID of the uploaded or updated file.
        """
        try:
            parent_id = self._get_or_create_path_id(parent_path, root_folder_id)
            if not parent_id:
                logger.error(f"Could not resolve or create parent path for upload: {parent_path}")
                return "" # Could not resolve or create parent path

            file_id = self.get_file_id_by_name(name, parent_id)
            media_body = MediaIoBaseUpload(BytesIO(content), mimetype='application/octet-stream', resumable=True)

            if file_id:
                logger.info(f"Updating existing file '{name}' with ID: {file_id}")
                file = self.service.files().update(
                    fileId=file_id,
                    media_body=media_body,
                    fields='id'
                ).execute()
            else:
                logger.info(f"Creating new file '{name}' under parent ID: {parent_id}")
                file_metadata = {
                    'name': name,
                    'parents': [parent_id]
                }
                file = self.service.files().create(
                    body=file_metadata,
                    media_body=media_body,
                    fields='id'
                ).execute()
            logger.info(f"File '{name}' uploaded/updated with ID: {file.get('id')}")
            return file.get('id')
        except HttpError as error:
            logger.error(f"An HttpError occurred during upload_file: {error}")
            return ""

    def delete_file(self, file_id: str):
        """Deletes a file or folder from Google Drive.

        Args:
            file_id: The ID of the file or folder to delete.
        """
        try:
            self.service.files().delete(fileId=file_id).execute()
            logger.info(f"File with ID {file_id} deleted successfully.")
        except HttpError as error:
            logger.error(f"An HttpError occurred during delete_file: {error}")

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