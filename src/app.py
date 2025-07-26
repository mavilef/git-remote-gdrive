import logging
import os
import sys
from .git_remote import GitRemote
from .google_drive_client_impl import GoogleDriveClientImpl
from argparse import ArgumentParser

from .utils import validate_google_drive_url

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
            - googledrive://FOLDER_ID
            - gd::FOLDER_ID
            - gdrive::FOLDER_ID
            - googledrive::FOLDER_ID""",
    )

    args = parser.parse_args()

    try:
        folder_id = validate_google_drive_url(getattr(args, "remote-url"))
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
    while True:
        try:
            command = input()
            logger.info(f"Received command: {command}")
        except EOFError:
            logger.info("EOF received, breaking loop.")
            break # End of input, typically when Git closes the pipe

        if command == "capabilities":
            logger.info("Handling capabilities command.")
            for capability in git_remote.get_capabilities():
                print(capability)
            print() # Blank line to end capabilities
            logger.info("Capabilities command handled.")
        elif command == "list" or command == "list for-push":
            logger.info(f"Handling {command} command.")
            for ref in git_remote.list_refs():
                print(ref)
            print() # Blank line to end list
            logger.info(f"{command} command handled.")
        elif command.startswith("push"):
            logger.info("Handling push command.")
            # Check if refspec is on the same line as 'push'
            if ':' in command:
                parts = command.split(' ', 1) # Split only on the first space
                if len(parts) > 1:
                    refspec = parts[1]
                    src, dst = refspec.split(':')
                    logger.info(f"Pushing {src} to {dst} (from same line)")
                    git_remote.push(src, dst)
            
            # Continue to read subsequent lines for more refspecs
            while True:
                line = input()
                logger.info(f"Received push line: {line}")
                if not line:
                    break # Blank line signals end of push requests
                src, dst = line.split(':')
                logger.info(f"Pushing {src} to {dst}")
                git_remote.push(src, dst)
            print() # Blank line to end push operation
            logger.info("Push command handled.")
        elif command == "":
            logger.info("Empty command received, breaking loop.")
            break
        else:
            logger.warning(f"Unknown command received: {command}")
            print() # Crucial: Print blank line for unknown commands to prevent hang

if __name__ == "__main__":
    main()