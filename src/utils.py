import re

def get_folder_id_from_google_drive_url(url: str) -> str:
    """Validate google drive url, returning the folder id


    Args:
        url (str): URL Containing the folder id, you can use the following formats:
            - gd://FOLDER_ID
            - gdrive://FOLDER_ID
            - googledrive://FOLDER_ID
            - gd::FOLDER_ID
            - gdrive::FOLDER_ID
            - googledrive::FOLDER_ID

    Returns:
        str: the folder id

    Raises:
        ValueError: if the url is not valid
    """

    pattern = r"^(?:gd|gdrive|googledrive)(?:://|::)([A-Za-z0-9._-]+)$"
    match = re.match(pattern, url)

    if match:
        folder_id = match.group(1)
        if folder_id: # Ensure FOLDER_ID is not empty
            return folder_id
        else:
            raise ValueError(
                f"Invalid Google Drive URL: '{url}'. FOLDER_ID cannot be empty."
            )
    else:
        raise ValueError(
            f"Invalid Google Drive URL format: '{url}'. "
            "Supported formats: "
            "gd://FOLDER_ID, gdrive://FOLDER_ID, googledrive://FOLDER_ID, "
            "gd::FOLDER_ID, gdrive::FOLDER_ID, googledrive::FOLDER_ID"
        )
