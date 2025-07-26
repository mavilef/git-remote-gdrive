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

    @abstractmethod
    def upload_file(self, name: str, content: bytes, parent_id: str) -> str:
        """Uploads a file to Google Drive or updates an existing one.

        If a file with the given name already exists in the parent folder,
        its content will be updated. Otherwise, a new file will be created.

        Args:
            name: The name of the file.
            content: The content of the file as bytes.
            parent_id: The ID of the parent folder where the file should be.

        Returns:
            The ID of the uploaded or updated file.
        """
        pass

    @abstractmethod
    def get_file_id_by_name(self, name: str, parent_id: str) -> str | None:
        """Gets the ID of a file or folder by its name within a specific parent folder.

        Args:
            name: The name of the file or folder.
            parent_id: The ID of the parent folder to search within.

        Returns:
            The ID of the file/folder if found, otherwise None.
        """
        pass

    @abstractmethod
    def create_folder(self, name: str, parent_id: str) -> str:
        """Creates a new folder on Google Drive.

        Args:
            name: The name of the new folder.
            parent_id: The ID of the parent folder where the new folder should be created.

        Returns:
            The ID of the newly created folder.
        """
        pass

    @abstractmethod
    def delete_file(self, file_id: str):
        """Deletes a file or folder from Google Drive.

        Args:
            file_id: The ID of the file or folder to delete.
        """
        pass