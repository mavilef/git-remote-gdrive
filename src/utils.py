import re
import logging

#configure logging as the app.py
logger = logging.getLogger(__name__)

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

def process_command(command: str, command_function_map: dict[re.Pattern, callable]) -> str:
    """Process a command and return the result.

    Args:
        command (str): The command to process.
        command_function_map (dict[re.Pattern, Callable]): A mapping of regex patterns to
command functions. The regex must include the groups, which will be passed as arguments
to the function.

    Returns:
        str: The result of the command execution.
    """
    command = command.strip()
    for pattern, func in command_function_map.items():
        match = pattern.match(command)
        if match:
            args = match.groups()
            print(args)
            return func(*args)
    return f"Unknown command: {command}"
