from abc import ABC, abstractmethod

class GoogleDriveClient(ABC):
    @abstractmethod
    def list_files(self, folder_id: str) -> list[str]:
        """Lists files within a specified Google Drive folder.

        Args:
            folder_id: The ID of the Google Drive folder.

        Returns:
            A list of file names within the folder.
        """
        pass

    @abstractmethod
    def read_file_content(self, file_path: str) -> bytes:
        """Reads the content of a file from Google Drive.

        Args:
            file_path: The path to the file on Google Drive.

        Returns:
            The content of the file as bytes.
        """
        pass