from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import ManifestError


FORMAT_VERSION = 1
SUPPORTED_OBJECT_FORMAT = "sha1"
_OID_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"manifest field '{field_name}' must be a non-empty string")
    return value


def _validate_oid(value: Any, field_name: str) -> str:
    oid = _require_string(value, field_name)
    if not _OID_RE.fullmatch(oid):
        raise ManifestError(f"manifest field '{field_name}' is not a SHA-1 object ID")
    return oid


def _valid_ref_name(name: str) -> bool:
    if not name.startswith("refs/") or name.endswith(("/", ".")):
        return False
    if ".." in name or "@{" in name or "//" in name:
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        return False
    if any(character in " ~^:?*[\\" for character in name):
        return False
    components = name.split("/")
    return all(
        component
        and not component.startswith(".")
        and not component.endswith(".lock")
        for component in components
    )


@dataclass(frozen=True)
class BundleRecord:
    file_id: str
    name: str
    sha256: str
    size: int
    created_at: str
    tips: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Any) -> "BundleRecord":
        if not isinstance(value, dict):
            raise ManifestError("each bundle entry must be an object")

        sha256 = _require_string(value.get("sha256"), "bundles[].sha256")
        if not _SHA256_RE.fullmatch(sha256):
            raise ManifestError("manifest bundle checksum is not SHA-256")

        size = value.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ManifestError("manifest bundle size must be a non-negative integer")

        tips = value.get("tips", [])
        prerequisites = value.get("prerequisites", [])
        if not isinstance(tips, list) or not isinstance(prerequisites, list):
            raise ManifestError("manifest bundle tips and prerequisites must be arrays")

        return cls(
            file_id=_require_string(value.get("file_id"), "bundles[].file_id"),
            name=_require_string(value.get("name"), "bundles[].name"),
            sha256=sha256,
            size=size,
            created_at=_require_string(value.get("created_at"), "bundles[].created_at"),
            tips=tuple(_validate_oid(oid, "bundles[].tips[]") for oid in tips),
            prerequisites=tuple(
                _validate_oid(oid, "bundles[].prerequisites[]")
                for oid in prerequisites
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "sha256": self.sha256,
            "size": self.size,
            "created_at": self.created_at,
            "tips": list(self.tips),
            "prerequisites": list(self.prerequisites),
        }


@dataclass
class Manifest:
    generation: int = 0
    object_format: str = SUPPORTED_OBJECT_FORMAT
    head: str | None = None
    refs: dict[str, str] = field(default_factory=dict)
    bundles: list[BundleRecord] = field(default_factory=list)
    format_version: int = FORMAT_VERSION

    @classmethod
    def empty(cls) -> "Manifest":
        return cls()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Manifest":
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"remote manifest is not valid UTF-8 JSON: {exc}") from exc

        if not isinstance(value, dict):
            raise ManifestError("remote manifest root must be an object")

        format_version = value.get("format_version")
        if format_version != FORMAT_VERSION:
            raise ManifestError(
                f"unsupported remote format version {format_version!r}; expected {FORMAT_VERSION}"
            )

        generation = value.get("generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
            raise ManifestError("manifest generation must be a non-negative integer")

        object_format = value.get("object_format")
        if object_format != SUPPORTED_OBJECT_FORMAT:
            raise ManifestError(
                f"unsupported Git object format {object_format!r}; only SHA-1 is supported"
            )

        refs_value = value.get("refs")
        if not isinstance(refs_value, dict):
            raise ManifestError("manifest refs must be an object")
        refs: dict[str, str] = {}
        for name, oid in refs_value.items():
            if not isinstance(name, str) or not _valid_ref_name(name):
                raise ManifestError(f"invalid remote ref name {name!r}")
            refs[name] = _validate_oid(oid, f"refs.{name}")

        head = value.get("head")
        if head is not None:
            if not isinstance(head, str) or head not in refs or not head.startswith("refs/heads/"):
                raise ManifestError("manifest HEAD must name an existing branch")

        bundles_value = value.get("bundles")
        if not isinstance(bundles_value, list):
            raise ManifestError("manifest bundles must be an array")

        return cls(
            format_version=format_version,
            generation=generation,
            object_format=object_format,
            head=head,
            refs=refs,
            bundles=[BundleRecord.from_dict(bundle) for bundle in bundles_value],
        )

    def to_bytes(self) -> bytes:
        value = {
            "format_version": self.format_version,
            "generation": self.generation,
            "object_format": self.object_format,
            "head": self.head,
            "refs": dict(sorted(self.refs.items())),
            "bundles": [bundle.to_dict() for bundle in self.bundles],
        }
        return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")

    def copy(self) -> "Manifest":
        return Manifest(
            format_version=self.format_version,
            generation=self.generation,
            object_format=self.object_format,
            head=self.head,
            refs=dict(self.refs),
            bundles=list(self.bundles),
        )
