from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import DriveConflictError


FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
BINARY_MIME_TYPE = "application/octet-stream"
ProgressCallback = Callable[[int], None]


@dataclass(frozen=True)
class DriveItem:
    id: str
    name: str
    mime_type: str
    version: str | None = None
    size: int | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME_TYPE


class GoogleDriveClient(ABC):
    @abstractmethod
    def find_child(self, parent_id: str, name: str) -> DriveItem | None:
        """Return the uniquely named child, or None when it does not exist."""

    @abstractmethod
    def create_folder(self, parent_id: str, name: str) -> DriveItem:
        """Create a folder directly below parent_id."""

    @abstractmethod
    def download_bytes(self, file_id: str) -> bytes:
        """Download a small file into memory."""

    @abstractmethod
    def download_to_path(
        self,
        file_id: str,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> None:
        """Stream a file into destination, reporting cumulative bytes received."""

    @abstractmethod
    def upload_bytes(
        self,
        parent_id: str,
        name: str,
        content: bytes,
        *,
        mime_type: str,
        existing_file_id: str | None = None,
    ) -> DriveItem:
        """Create or replace a small file."""

    @abstractmethod
    def upload_path(
        self,
        parent_id: str,
        name: str,
        source: Path,
        *,
        mime_type: str = BINARY_MIME_TYPE,
        progress: ProgressCallback | None = None,
    ) -> DriveItem:
        """Stream a new Drive file, reporting cumulative bytes acknowledged."""

    @abstractmethod
    def delete_file(self, file_id: str) -> None:
        """Delete a file or folder."""

    def ensure_folder(self, parent_id: str, name: str) -> DriveItem:
        item = self.find_child(parent_id, name)
        if item is None:
            return self.create_folder(parent_id, name)
        if not item.is_folder:
            raise DriveConflictError(
                f"'{name}' exists in the remote folder but is not a folder"
            )
        return item
