from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable

from .errors import GitCommandFailure


class GitRepository:
    def __init__(
        self,
        cwd: str | Path | None = None,
        *,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.cwd = Path(cwd) if cwd is not None else None
        self.environment = environment

    def _run(
        self,
        arguments: Iterable[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", *arguments]
        environment = os.environ.copy()
        if self.environment:
            environment.update(self.environment)
        process = subprocess.run(
            command,
            cwd=self.cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if check and process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or "unknown Git error"
            raise GitCommandFailure(f"{' '.join(command)} failed: {detail}")
        return process

    def object_format(self) -> str:
        return self._run(["rev-parse", "--show-object-format"]).stdout.strip()

    def resolve_object(self, revision: str) -> str:
        if not revision:
            raise GitCommandFailure("cannot resolve an empty Git revision")
        return self._run(
            ["rev-parse", "--verify", f"{revision}^{{object}}"]
        ).stdout.strip()

    def object_type(self, object_id: str) -> str:
        return self._run(["cat-file", "-t", object_id]).stdout.strip()

    def has_object(self, object_id: str) -> bool:
        return self._run(
            ["cat-file", "-e", f"{object_id}^{{object}}"], check=False
        ).returncode == 0

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        process = self._run(
            ["merge-base", "--is-ancestor", ancestor, descendant], check=False
        )
        if process.returncode == 0:
            return True
        if process.returncode == 1:
            return False
        detail = process.stderr.strip() or "could not compare Git history"
        raise GitCommandFailure(detail)

    def valid_ref_name(self, ref_name: str) -> bool:
        return self._run(["check-ref-format", ref_name], check=False).returncode == 0

    @staticmethod
    def _deduplicate(values: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(values))

    def has_new_objects(
        self, positive_refs: Iterable[str], exclusion_oids: Iterable[str]
    ) -> bool:
        positives = self._deduplicate(positive_refs)
        exclusions = self._deduplicate(exclusion_oids)
        if not positives:
            return False
        process = self._run(
            [
                "rev-list",
                "--objects",
                *positives,
                *(f"^{oid}" for oid in exclusions),
            ]
        )
        return bool(process.stdout.strip())

    def create_bundle(
        self,
        destination: Path,
        positive_refs: Iterable[str],
        exclusion_oids: Iterable[str],
    ) -> None:
        positives = self._deduplicate(positive_refs)
        exclusions = self._deduplicate(exclusion_oids)
        if not positives:
            raise GitCommandFailure("cannot create a bundle without source refs")
        self._run(
            [
                "bundle",
                "create",
                "--quiet",
                str(destination),
                *positives,
                *(f"^{oid}" for oid in exclusions),
            ]
        )

    def unbundle(self, bundle_path: Path) -> None:
        self._run(["bundle", "verify", "--quiet", str(bundle_path)])
        self._run(["bundle", "unbundle", str(bundle_path)])

    def objects_connected(self, object_ids: Iterable[str]) -> bool:
        ids = self._deduplicate(object_ids)
        if not ids:
            return True
        process = self._run(
            ["rev-list", "--objects", "--missing=print", *ids]
        )
        return not any(line.startswith("?") for line in process.stdout.splitlines())
