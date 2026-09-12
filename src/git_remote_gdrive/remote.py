from __future__ import annotations

import logging
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import DriveError, GitCommandFailure, GitRemoteGDriveError, ProtocolError
from .git_repository import GitRepository
from .manifest import BundleRecord, Manifest, SUPPORTED_OBJECT_FORMAT
from .store import DriveRemoteStore, LoadedManifest, sha256_file


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchRequest:
    object_id: str
    ref_name: str


@dataclass(frozen=True)
class PushRequest:
    source: str
    destination: str
    force: bool = False

    @classmethod
    def parse(cls, command: str) -> "PushRequest":
        if not command.startswith("push "):
            raise ProtocolError(f"invalid push command: {command!r}")
        refspec = command[5:]
        force = refspec.startswith("+")
        if force:
            refspec = refspec[1:]
        if ":" not in refspec:
            raise ProtocolError(f"push refspec has no destination: {refspec!r}")
        source, destination = refspec.split(":", 1)
        if not destination:
            raise ProtocolError(f"push refspec has an empty destination: {refspec!r}")
        return cls(source=source, destination=destination, force=force)


@dataclass(frozen=True)
class PushResult:
    destination: str
    ok: bool
    message: str | None = None


@dataclass(frozen=True)
class _AcceptedChange:
    request: PushRequest
    old_object_id: str | None
    new_object_id: str | None


class GitDriveRemote:
    def __init__(self, store: DriveRemoteStore, repository: GitRepository) -> None:
        self.store = store
        self.repository = repository
        self._loaded: LoadedManifest | None = None

    def _manifest(self) -> LoadedManifest:
        if self._loaded is None:
            self._loaded = self.store.load_manifest()
        return self._loaded

    def _check_object_format(self, manifest: Manifest) -> None:
        local_format = self.repository.object_format()
        if local_format != SUPPORTED_OBJECT_FORMAT:
            raise GitRemoteGDriveError(
                f"local repository uses unsupported object format {local_format!r}"
            )
        if manifest.object_format != local_format:
            raise GitRemoteGDriveError(
                f"remote uses {manifest.object_format}, local repository uses {local_format}"
            )

    def list_refs(self) -> list[str]:
        manifest = self._manifest().manifest
        lines: list[str] = []
        if manifest.head:
            lines.append(f"@{manifest.head} HEAD")
        lines.extend(f"{oid} {name}" for name, oid in sorted(manifest.refs.items()))
        return lines

    def fetch(self, requests: list[FetchRequest], *, check_connectivity: bool) -> None:
        manifest = self._manifest().manifest
        self._check_object_format(manifest)

        requested_ids: list[str] = []
        for request in requests:
            ref_name = manifest.head if request.ref_name == "HEAD" else request.ref_name
            expected = manifest.refs.get(ref_name or "")
            if expected != request.object_id:
                raise ProtocolError(
                    f"Git requested object {request.object_id} for unknown or stale ref "
                    f"{request.ref_name}"
                )
            requested_ids.append(request.object_id)

        if not all(self.repository.has_object(oid) for oid in requested_ids):
            for bundle in manifest.bundles:
                if all(self.repository.has_object(oid) for oid in requested_ids):
                    break
                if bundle.tips and all(
                    self.repository.has_object(oid) for oid in bundle.tips
                ):
                    continue
                bundle_path = self.store.cached_bundle(bundle)
                self.repository.unbundle(bundle_path)

        missing = [oid for oid in requested_ids if not self.repository.has_object(oid)]
        if missing:
            raise GitRemoteGDriveError(
                "remote bundle history is incomplete; missing objects: " + ", ".join(missing)
            )
        if check_connectivity and not self.repository.objects_connected(requested_ids):
            raise GitRemoteGDriveError("fetched Git history is not fully connected")

    @staticmethod
    def _choose_head(manifest: Manifest) -> str | None:
        branches = sorted(name for name in manifest.refs if name.startswith("refs/heads/"))
        if manifest.head in branches:
            return manifest.head
        for preferred in ("refs/heads/main", "refs/heads/master"):
            if preferred in branches:
                return preferred
        return branches[0] if branches else None

    def _validate_push(
        self,
        request: PushRequest,
        manifest: Manifest,
        *,
        force_option: bool,
    ) -> tuple[_AcceptedChange | None, PushResult]:
        destination = request.destination
        if not self.repository.valid_ref_name(destination):
            return None, PushResult(destination, False, "invalid destination ref")

        old_object_id = manifest.refs.get(destination)
        if not request.source:
            return (
                _AcceptedChange(request, old_object_id, None),
                PushResult(destination, True),
            )

        try:
            new_object_id = self.repository.resolve_object(request.source)
            object_type = self.repository.object_type(new_object_id)
        except GitCommandFailure as exc:
            return None, PushResult(destination, False, str(exc))

        if destination.startswith("refs/heads/") and object_type != "commit":
            return None, PushResult(
                destination, False, "a branch must point to a commit"
            )

        forced = request.force or force_option
        if old_object_id and old_object_id != new_object_id and not forced:
            if destination.startswith("refs/heads/"):
                if not self.repository.has_object(old_object_id):
                    return None, PushResult(
                        destination,
                        False,
                        "remote tip is missing locally; fetch before pushing",
                    )
                try:
                    fast_forward = self.repository.is_ancestor(
                        old_object_id, new_object_id
                    )
                except GitCommandFailure as exc:
                    return None, PushResult(destination, False, str(exc))
                if not fast_forward:
                    return None, PushResult(destination, False, "non-fast-forward")
            else:
                return None, PushResult(
                    destination, False, "remote ref update requires force"
                )

        return (
            _AcceptedChange(request, old_object_id, new_object_id),
            PushResult(destination, True),
        )

    def push(
        self,
        requests: list[PushRequest],
        *,
        dry_run: bool,
        force_option: bool,
    ) -> list[PushResult]:
        loaded = self._manifest()
        self._check_object_format(loaded.manifest)
        updated = loaded.manifest.copy()

        results: list[PushResult] = []
        accepted: list[_AcceptedChange] = []
        seen_destinations: set[str] = set()
        for request in requests:
            if request.destination in seen_destinations:
                results.append(
                    PushResult(request.destination, False, "duplicate destination ref")
                )
                continue
            seen_destinations.add(request.destination)
            change, result = self._validate_push(
                request, loaded.manifest, force_option=force_option
            )
            results.append(result)
            if change is not None:
                accepted.append(change)

        changed = [
            change
            for change in accepted
            if change.old_object_id != change.new_object_id
        ]
        for change in changed:
            if change.new_object_id is None:
                updated.refs.pop(change.request.destination, None)
            else:
                updated.refs[change.request.destination] = change.new_object_id
        updated.head = self._choose_head(updated)

        if not changed or dry_run:
            return results

        positive_refs = [
            change.request.source
            for change in changed
            if change.new_object_id is not None
        ]
        exclusion_oids = sorted(
            {
                oid
                for oid in loaded.manifest.refs.values()
                if self.repository.has_object(oid)
            }
        )

        uploaded_file_id: str | None = None
        try:
            if positive_refs and self.repository.has_new_objects(
                positive_refs, exclusion_oids
            ):
                with tempfile.TemporaryDirectory(prefix="git-remote-gdrive-") as directory:
                    generation = loaded.manifest.generation + 1
                    bundle_name = (
                        f"bundle-{generation:08d}-{uuid.uuid4().hex}.bundle"
                    )
                    bundle_path = Path(directory) / bundle_name
                    self.repository.create_bundle(
                        bundle_path, positive_refs, exclusion_oids
                    )
                    checksum = sha256_file(bundle_path)
                    size = bundle_path.stat().st_size
                    item = self.store.upload_bundle(bundle_name, bundle_path)
                    uploaded_file_id = item.id
                    updated.bundles.append(
                        BundleRecord(
                            file_id=item.id,
                            name=bundle_name,
                            sha256=checksum,
                            size=size,
                            created_at=datetime.now(timezone.utc).isoformat(),
                            tips=tuple(
                                dict.fromkeys(
                                    change.new_object_id
                                    for change in changed
                                    if change.new_object_id is not None
                                )
                            ),
                            prerequisites=tuple(exclusion_oids),
                        )
                    )
        except Exception:
            if uploaded_file_id is not None:
                try:
                    self.store.delete_bundle(uploaded_file_id)
                except DriveError:
                    logger.warning(
                        "could not remove orphaned bundle %s", uploaded_file_id, exc_info=True
                    )
            raise

        # A failed save may have published the manifest before losing its response.
        # Keep its bundle once publication is attempted.
        updated.generation = loaded.manifest.generation + 1
        self._loaded = self.store.save_manifest(loaded, updated)

        return results
