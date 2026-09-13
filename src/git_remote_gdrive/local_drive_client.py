from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .errors import DriveConflictError, DriveError
from .google_drive_client import (
    BINARY_MIME_TYPE,
    FOLDER_MIME_TYPE,
    DriveItem,
    GoogleDriveClient,
    ProgressCallback,
)


class LocalDriveClient(GoogleDriveClient):
    """Filesystem-backed Drive implementation used by integration tests."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise DriveError(f"invalid Drive child name: {name!r}")

    def _path(self, file_id: str) -> Path:
        candidate = (self.base_path / file_id).resolve()
        try:
            candidate.relative_to(self.base_path)
        except ValueError as exc:
            raise DriveError(f"invalid local Drive file ID: {file_id!r}") from exc
        return candidate

    def _id(self, path: Path) -> str:
        return path.resolve().relative_to(self.base_path).as_posix()

    def _item(self, path: Path) -> DriveItem:
        stat = path.stat()
        return DriveItem(
            id=self._id(path),
            name=path.name,
            mime_type=FOLDER_MIME_TYPE if path.is_dir() else BINARY_MIME_TYPE,
            version=f"{stat.st_mtime_ns}:{stat.st_size}",
            size=None if path.is_dir() else stat.st_size,
        )

    def find_child(self, parent_id: str, name: str) -> DriveItem | None:
        self._validate_name(name)
        parent = self._path(parent_id)
        if not parent.is_dir():
            raise DriveError(f"local Drive parent folder does not exist: {parent_id}")
        child = parent / name
        return self._item(child) if child.exists() else None

    def create_folder(self, parent_id: str, name: str) -> DriveItem:
        self._validate_name(name)
        path = self._path(parent_id) / name
        try:
            path.mkdir()
        except FileExistsError as exc:
            raise DriveConflictError(f"local Drive child already exists: {path}") from exc
        except OSError as exc:
            raise DriveError(f"could not create local Drive folder {path}: {exc}") from exc
        return self._item(path)

    def download_bytes(self, file_id: str) -> bytes:
        try:
            return self._path(file_id).read_bytes()
        except OSError as exc:
            raise DriveError(f"could not read local Drive file {file_id}: {exc}") from exc

    def download_to_path(
        self,
        file_id: str,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> None:
        try:
            self._copy(self._path(file_id), destination, progress)
        except OSError as exc:
            raise DriveError(f"could not download local Drive file {file_id}: {exc}") from exc

    @staticmethod
    def _copy(
        source: Path, destination: Path, progress: ProgressCallback | None
    ) -> None:
        if progress is None:
            shutil.copyfile(source, destination)
            return
        transferred = 0
        with source.open("rb") as reader, destination.open("wb") as writer:
            while chunk := reader.read(8 * 1024 * 1024):
                writer.write(chunk)
                transferred += len(chunk)
                progress(transferred)

    def _atomic_copy(
        self,
        source: Path,
        destination: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            self._copy(source, temporary, progress)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def upload_bytes(
        self,
        parent_id: str,
        name: str,
        content: bytes,
        *,
        mime_type: str,
        existing_file_id: str | None = None,
    ) -> DriveItem:
        del mime_type
        self._validate_name(name)
        destination = (
            self._path(existing_file_id)
            if existing_file_id is not None
            else self._path(parent_id) / name
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return self._item(destination)

    def upload_path(
        self,
        parent_id: str,
        name: str,
        source: Path,
        *,
        mime_type: str = BINARY_MIME_TYPE,
        progress: ProgressCallback | None = None,
    ) -> DriveItem:
        del mime_type
        self._validate_name(name)
        destination = self._path(parent_id) / name
        if destination.exists():
            raise DriveConflictError(f"local Drive child already exists: {destination}")
        self._atomic_copy(source, destination, progress)
        return self._item(destination)

    def delete_file(self, file_id: str) -> None:
        path = self._path(file_id)
        try:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            raise DriveError(f"could not delete local Drive file {file_id}: {exc}") from exc
