from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import DriveConflictError, DriveError, ManifestError
from .google_drive_client import DriveItem, GoogleDriveClient
from .manifest import BundleRecord, Manifest


INTERNAL_FOLDER_NAME = ".git-remote-gdrive"
MANIFEST_FILE_NAME = "manifest.json"
BUNDLES_FOLDER_NAME = "bundles"


def default_cache_dir() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return base / "git-remote-gdrive"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class LoadedManifest:
    manifest: Manifest
    container: DriveItem | None
    manifest_item: DriveItem | None


class DriveRemoteStore:
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

    def load_manifest(self) -> LoadedManifest:
        container = self.client.find_child(self.root_folder_id, INTERNAL_FOLDER_NAME)
        if container is None:
            return LoadedManifest(Manifest.empty(), None, None)
        if not container.is_folder:
            raise DriveConflictError(
                f"remote metadata path '{INTERNAL_FOLDER_NAME}' is not a folder"
            )

        item = self.client.find_child(container.id, MANIFEST_FILE_NAME)
        if item is None:
            return LoadedManifest(Manifest.empty(), container, None)
        if item.is_folder:
            raise DriveConflictError("remote manifest path is a folder")

        try:
            manifest = Manifest.from_bytes(self.client.download_bytes(item.id))
        except ManifestError:
            raise
        except Exception as exc:
            raise DriveError(f"could not load remote manifest: {exc}") from exc
        return LoadedManifest(manifest, container, item)

    def _ensure_container(self) -> DriveItem:
        return self.client.ensure_folder(self.root_folder_id, INTERNAL_FOLDER_NAME)

    @staticmethod
    def _same_manifest_item(left: DriveItem | None, right: DriveItem | None) -> bool:
        if left is None or right is None:
            return left is right
        if left.id != right.id:
            return False
        if left.version is not None and right.version is not None:
            return left.version == right.version
        return True

    def save_manifest(self, expected: LoadedManifest, manifest: Manifest) -> LoadedManifest:
        current = self.load_manifest()
        if (
            current.manifest.generation != expected.manifest.generation
            or not self._same_manifest_item(current.manifest_item, expected.manifest_item)
        ):
            raise DriveConflictError(
                "remote changed during push; fetch and retry to avoid overwriting another update"
            )

        container = current.container or self._ensure_container()
        item = self.client.upload_bytes(
            container.id,
            MANIFEST_FILE_NAME,
            manifest.to_bytes(),
            mime_type="application/json",
            existing_file_id=(
                current.manifest_item.id if current.manifest_item is not None else None
            ),
        )
        return LoadedManifest(manifest, container, item)

    def upload_bundle(self, name: str, source: Path) -> DriveItem:
        container = self._ensure_container()
        bundles = self.client.ensure_folder(container.id, BUNDLES_FOLDER_NAME)
        if self.client.find_child(bundles.id, name) is not None:
            raise DriveConflictError(f"remote bundle already exists: {name}")
        return self.client.upload_path(bundles.id, name, source)

    def delete_bundle(self, file_id: str) -> None:
        self.client.delete_file(file_id)

    def cached_bundle(self, bundle: BundleRecord) -> Path:
        remote_key = hashlib.sha256(self.root_folder_id.encode("utf-8")).hexdigest()[:16]
        directory = self.cache_dir / remote_key
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{bundle.sha256}.bundle"

        if destination.is_file():
            if destination.stat().st_size == bundle.size and sha256_file(destination) == bundle.sha256:
                return destination
            destination.unlink()

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{bundle.sha256}.", dir=directory
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            self.client.download_to_path(bundle.file_id, temporary)
            actual_size = temporary.stat().st_size
            actual_checksum = sha256_file(temporary)
            if actual_size != bundle.size or actual_checksum != bundle.sha256:
                raise DriveError(
                    f"bundle '{bundle.name}' failed integrity verification"
                )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination
