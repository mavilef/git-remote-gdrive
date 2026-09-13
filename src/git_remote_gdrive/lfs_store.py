from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from .errors import DriveConflictError, DriveError
from .google_drive_client import DriveItem, GoogleDriveClient, ProgressCallback
from .store import INTERNAL_FOLDER_NAME, default_cache_dir, sha256_file


class LFSObjectStore:
    """Store immutable Git LFS objects separately from the Git manifest."""

    def __init__(
        self,
        client: GoogleDriveClient,
        root_folder_id: str,
        *,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.client = client
        self.root_folder_id = root_folder_id
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self._handoffs: list[Path] = []

    def close(self) -> None:
        for path in self._handoffs:
            path.unlink(missing_ok=True)
        self._handoffs.clear()

    @staticmethod
    def _validate(oid: str, size: int) -> None:
        if not isinstance(oid, str) or re.fullmatch(r"[0-9a-f]{64}", oid) is None:
            raise DriveError("LFS object ID must be a lowercase SHA-256 hash")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise DriveError("LFS object size must be a nonnegative integer")

    @staticmethod
    def _matches(path: Path, oid: str, size: int) -> bool:
        return (
            path.is_file()
            and path.stat().st_size == size
            and sha256_file(path) == oid
        )

    def _object_folder(self, oid: str, *, create: bool) -> DriveItem | None:
        parent_id = self.root_folder_id
        for name in (INTERNAL_FOLDER_NAME, "lfs", "objects", oid[:2]):
            if create:
                folder = self.client.ensure_folder(parent_id, name)
            else:
                folder = self.client.find_child(parent_id, name)
                if folder is None:
                    return None
                if not folder.is_folder:
                    raise DriveConflictError(
                        f"remote LFS path '{name}' is not a folder"
                    )
            parent_id = folder.id
        return folder

    def _cache_path(self, oid: str) -> Path:
        return self.cache_dir / "lfs" / "objects" / oid[:2] / oid

    def _download_object(
        self,
        item: DriveItem,
        oid: str,
        size: int,
        *,
        progress: ProgressCallback | None = None,
    ) -> Path:
        if item.is_folder:
            raise DriveConflictError(f"remote LFS object '{oid}' is a folder")
        destination = self._cache_path(oid)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{oid}.", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            self.client.download_to_path(item.id, temporary, progress=progress)
            if not self._matches(temporary, oid, size):
                raise DriveError(f"LFS object '{oid}' failed integrity verification")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def upload(
        self,
        oid: str,
        size: int,
        source: Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> None:
        self._validate(oid, size)
        source = Path(source)
        if not self._matches(source, oid, size):
            raise DriveError(f"local LFS object '{oid}' failed integrity verification")

        folder = self._object_folder(oid, create=True)
        assert folder is not None
        existing = self.client.find_child(folder.id, oid)
        if existing is not None:
            # Verify remote bytes even when a valid local cached copy exists.
            self._download_object(existing, oid, size, progress=progress)
            return
        # A lost upload response can still mean success. Leave the object in place
        # so a retry can verify and reuse it.
        self.client.upload_path(folder.id, oid, source, progress=progress)

    def download(
        self, oid: str, size: int, *, progress: ProgressCallback | None = None
    ) -> Path:
        self._validate(oid, size)
        destination = self._cache_path(oid)
        if not self._matches(destination, oid, size):
            folder = self._object_folder(oid, create=False)
            item = self.client.find_child(folder.id, oid) if folder is not None else None
            if item is None:
                raise DriveError(f"LFS object '{oid}' was not found in the remote")
            destination = self._download_object(item, oid, size, progress=progress)

        # Git LFS moves the returned file into its own object storage. Give each
        # request a disposable copy so the shared cache remains available.
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{oid}.handoff.", dir=destination.parent
        )
        os.close(descriptor)
        handoff = Path(temporary_name)
        try:
            shutil.copyfile(destination, handoff)
        except BaseException:
            handoff.unlink(missing_ok=True)
            raise
        self._handoffs.append(handoff)
        return handoff
