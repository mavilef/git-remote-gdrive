from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence, TextIO

from .errors import ConfigurationError
from .git_repository import GitRepository
from .google_drive_client import GoogleDriveClient
from .google_drive_client_impl import GoogleDriveClientImpl
from .local_drive_client import LocalDriveClient
from .log_config import configure_logging
from .protocol import RemoteHelperProtocol
from .remote import GitDriveRemote
from .store import DriveRemoteStore
from .utils import get_folder_id_from_google_drive_url


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-remote-gdrive",
        description="Use a Google Drive folder as a Git remote",
    )
    parser.add_argument("remote_name", help="name assigned to the remote by Git")
    parser.add_argument("remote_url", help="gd://FOLDER_ID or gdrive::FOLDER_ID")
    return parser


def build_drive_client() -> GoogleDriveClient:
    local_root = os.environ.get("GDRIVE_LOCAL_ROOT")
    if local_root:
        return LocalDriveClient(local_root)
    credentials_path = os.environ.get("GDRIVE_CREDENTIALS_PATH")
    if not credentials_path:
        raise ConfigurationError(
            "GDRIVE_CREDENTIALS_PATH is not set; point it to your OAuth desktop "
            "client credentials JSON file"
        )
    return GoogleDriveClientImpl(
        credentials_path,
        token_path=os.environ.get("GDRIVE_TOKEN_PATH"),
    )


def _build_remote(folder_id: str) -> GitDriveRemote:
    cache_path = os.environ.get("GDRIVE_CACHE_DIR")
    store = DriveRemoteStore(
        build_drive_client(),
        folder_id,
        cache_dir=Path(cache_path).expanduser() if cache_path else None,
    )
    return GitDriveRemote(store, GitRepository())


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    configure_logging()
    args = _parser().parse_args(argv)
    try:
        folder_id = get_folder_id_from_google_drive_url(args.remote_url)
    except ValueError as exc:
        _parser().error(str(exc))

    protocol = RemoteHelperProtocol(
        lambda: _build_remote(folder_id),
        stdin or sys.stdin,
        stdout or sys.stdout,
    )
    return protocol.run()


if __name__ == "__main__":
    raise SystemExit(main())
