import logging
import os
import sys
from .git_remote import GitRemote
from .google_drive_client_impl import GoogleDriveClientImpl
from argparse import ArgumentParser

from .utils import get_folder_id_from_google_drive_url

logger = logging.getLogger(__name__)
formatter = logging.Formatter("[%(asctime)s][%(name)s][%(levelname)s] %(message)s")

# Set logging level to INFO for debugging
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stderr)
handler.setLevel(logging.INFO)

handler.setFormatter(formatter)
logger.addHandler(handler)

def main():
    parser = ArgumentParser(
        prog="git-gdrive-remote",
        description="""A helper to use google drive as remote in git""",
    )

    parser.add_argument("remote-name", help="Remote name")

    parser.add_argument(
        "remote-url",
        help="""Google drive folder id, you can use the following formats:
            - gd://FOLDER_ID
            - gdrive://FOLDER_ID
            - googledrive://FOLDER_ID""",
    )

    args = parser.parse_args()

    # Log the received arguments
    logger.info(f"Received arguments: {args}")
    try:
        folder_id = get_folder_id_from_google_drive_url(getattr(args, "remote-url"))
        logger.info(f"Validated Google Drive Folder ID: {folder_id}")
        logger.info(f"Received remote name: {getattr(args, 'remote-name')}")
    except ValueError as e:
        logger.error(e)
        sys.exit(1)

    credentials_path = os.environ.get("GDRIVE_CREDENTIALS_PATH")
    if not credentials_path:
        logger.error("GDRIVE_CREDENTIALS_PATH environment variable not set.")
        sys.exit(1)

    client = GoogleDriveClientImpl(credentials_path)
    git_remote = GitRemote(folder_id, client)

    logger.info("Starting command loop...")
        
if __name__ == "__main__":
    main()
