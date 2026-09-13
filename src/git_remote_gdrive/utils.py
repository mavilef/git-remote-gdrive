from __future__ import annotations

import re
from urllib.parse import urlparse


_FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_REMOTE_URL_RE = re.compile(
    r"^(?:gd|gdrive|googledrive)(?:://|::)([A-Za-z0-9._-]+)$"
)


def get_folder_id_from_google_drive_url(value: str) -> str:
    """Extract a Drive folder ID from a helper URL, raw ID, or Drive web URL."""
    match = _REMOTE_URL_RE.fullmatch(value)
    if match:
        return match.group(1)

    if _FOLDER_ID_RE.fullmatch(value):
        return value

    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc == "drive.google.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 3 and parts[:2] == ["drive", "folders"]:
            folder_id = parts[2]
            if _FOLDER_ID_RE.fullmatch(folder_id):
                return folder_id

    raise ValueError(
        "invalid Google Drive remote. Use gd://FOLDER_ID, gdrive::FOLDER_ID, "
        "a raw folder ID, or a drive.google.com/drive/folders/FOLDER_ID URL"
    )
