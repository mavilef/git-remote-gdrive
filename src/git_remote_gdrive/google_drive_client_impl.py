from __future__ import annotations

import logging
import os
import sys
from contextlib import redirect_stdout
from io import BytesIO
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import (
    DEFAULT_CHUNK_SIZE,
    MediaFileUpload,
    MediaIoBaseDownload,
    MediaIoBaseUpload,
)

from .errors import ConfigurationError, DriveConflictError, DriveError
from .google_drive_client import (
    BINARY_MIME_TYPE,
    FOLDER_MIME_TYPE,
    DriveItem,
    GoogleDriveClient,
    ProgressCallback,
)


logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/drive"]
TRANSFER_CHUNK_SIZE = 8 * 1024 * 1024


def default_token_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "git-remote-gdrive" / "token.json"


def _escape_query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDriveClientImpl(GoogleDriveClient):
    def __init__(
        self,
        credentials_path: str | Path,
        token_path: str | Path | None = None,
        *,
        service: Any | None = None,
    ) -> None:
        self.credentials_path = Path(credentials_path).expanduser()
        self.token_path = (
            Path(token_path).expanduser() if token_path is not None else default_token_path()
        )

        if service is not None:
            self.service = service
            return

        if not self.credentials_path.is_file():
            raise ConfigurationError(
                f"Google OAuth credentials file not found: {self.credentials_path}"
            )

        credentials = self._authenticate()
        try:
            self.service = build(
                "drive",
                "v3",
                credentials=credentials,
                cache_discovery=False,
            )
        except Exception as exc:
            raise DriveError(f"could not initialize the Google Drive API: {exc}") from exc

    def _authenticate(self) -> Credentials:
        credentials: Credentials | None = None

        if self.token_path.is_file():
            try:
                credentials = Credentials.from_authorized_user_file(
                    self.token_path, SCOPES
                )
            except (OSError, ValueError) as exc:
                raise ConfigurationError(
                    f"could not read OAuth token {self.token_path}: {exc}"
                ) from exc

        try:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            elif not credentials or not credentials.valid:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                with redirect_stdout(sys.stderr):
                    credentials = flow.run_local_server(port=0)
        except Exception as exc:
            raise ConfigurationError(f"Google OAuth authorization failed: {exc}") from exc

        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(credentials.to_json(), encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(
                f"could not save OAuth token {self.token_path}: {exc}"
            ) from exc
        try:
            self.token_path.chmod(0o600)
        except OSError:
            logger.debug("could not restrict token file permissions", exc_info=True)
        return credentials

    @staticmethod
    def _item(value: dict[str, Any]) -> DriveItem:
        raw_size = value.get("size")
        size = int(raw_size) if raw_size is not None else None
        raw_version = value.get("version")
        version = str(raw_version) if raw_version is not None else None
        return DriveItem(
            id=value["id"],
            name=value["name"],
            mime_type=value["mimeType"],
            version=version,
            size=size,
        )

    @staticmethod
    def _fields() -> str:
        return "id,name,mimeType,size,modifiedTime,version"

    def find_child(self, parent_id: str, name: str) -> DriveItem | None:
        escaped_parent = _escape_query_literal(parent_id)
        escaped_name = _escape_query_literal(name)
        query = (
            f"name = '{escaped_name}' and '{escaped_parent}' in parents "
            "and trashed = false"
        )
        items = []
        page_token = None
        try:
            while True:
                response = (
                    self.service.files()
                    .list(
                        q=query,
                        spaces="drive",
                        pageSize=2,
                        pageToken=page_token,
                        fields=f"nextPageToken,incompleteSearch,files({self._fields()})",
                        includeItemsFromAllDrives=True,
                        supportsAllDrives=True,
                    )
                    .execute()
                )
                if response.get("incompleteSearch"):
                    raise DriveError("Google Drive search was incomplete; retry the operation")
                items.extend(response.get("files", []))
                page_token = response.get("nextPageToken")
                if len(items) > 1 or not page_token:
                    break
        except HttpError as exc:
            raise DriveError(f"could not find '{name}' in Google Drive: {exc}") from exc

        if not items:
            return None
        if len(items) > 1:
            raise DriveConflictError(
                f"more than one file named '{name}' exists in the selected Drive folder"
            )
        return self._item(items[0])

    def create_folder(self, parent_id: str, name: str) -> DriveItem:
        metadata = {
            "name": name,
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [parent_id],
        }
        try:
            value = (
                self.service.files()
                .create(
                    body=metadata,
                    fields=self._fields(),
                    supportsAllDrives=True,
                )
                .execute()
            )
        except HttpError as exc:
            raise DriveError(f"could not create Drive folder '{name}': {exc}") from exc
        return self._item(value)

    def download_bytes(self, file_id: str) -> bytes:
        target = BytesIO()
        self._download(file_id, target)
        return target.getvalue()

    def download_to_path(
        self,
        file_id: str,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> None:
        try:
            with destination.open("wb") as target:
                self._download(file_id, target, progress=progress)
        except OSError as exc:
            raise DriveError(f"could not write downloaded bundle to {destination}: {exc}") from exc

    def _download(
        self,
        file_id: str,
        target: Any,
        *,
        progress: ProgressCallback | None = None,
    ) -> None:
        try:
            request = self.service.files().get_media(
                fileId=file_id, supportsAllDrives=True
            )
            downloader = MediaIoBaseDownload(
                target, request,
                chunksize=TRANSFER_CHUNK_SIZE if progress is not None else DEFAULT_CHUNK_SIZE,
            )
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if progress is not None and status is not None:
                    progress(status.resumable_progress)
        except HttpError as exc:
            raise DriveError(f"could not download Drive file {file_id}: {exc}") from exc

    def upload_bytes(
        self,
        parent_id: str,
        name: str,
        content: bytes,
        *,
        mime_type: str,
        existing_file_id: str | None = None,
    ) -> DriveItem:
        media = MediaIoBaseUpload(BytesIO(content), mimetype=mime_type, resumable=False)
        try:
            if existing_file_id:
                request = self.service.files().update(
                    fileId=existing_file_id,
                    body={"name": name},
                    media_body=media,
                    fields=self._fields(),
                    supportsAllDrives=True,
                )
            else:
                request = self.service.files().create(
                    body={"name": name, "parents": [parent_id]},
                    media_body=media,
                    fields=self._fields(),
                    supportsAllDrives=True,
                )
            return self._item(request.execute())
        except HttpError as exc:
            raise DriveError(f"could not upload Drive file '{name}': {exc}") from exc

    def upload_path(
        self,
        parent_id: str,
        name: str,
        source: Path,
        *,
        mime_type: str = BINARY_MIME_TYPE,
        progress: ProgressCallback | None = None,
    ) -> DriveItem:
        media = MediaFileUpload(
            str(source),
            mimetype=mime_type,
            resumable=True,
            chunksize=TRANSFER_CHUNK_SIZE if progress is not None else DEFAULT_CHUNK_SIZE,
        )
        try:
            request = self.service.files().create(
                body={"name": name, "parents": [parent_id]},
                media_body=media,
                fields=self._fields(),
                supportsAllDrives=True,
            )
            value = None
            while value is None:
                status, value = request.next_chunk()
                if progress is not None and status is not None:
                    progress(status.resumable_progress)
            if progress is not None:
                progress(media.size())
        except HttpError as exc:
            raise DriveError(f"could not upload bundle '{name}': {exc}") from exc
        return self._item(value)

    def delete_file(self, file_id: str) -> None:
        try:
            (
                self.service.files()
                .delete(fileId=file_id, supportsAllDrives=True)
                .execute()
            )
        except HttpError as exc:
            raise DriveError(f"could not delete Drive file {file_id}: {exc}") from exc
