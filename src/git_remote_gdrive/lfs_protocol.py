from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from .errors import ProtocolError

if TYPE_CHECKING:
    from .lfs_store import LFSObjectStore


logger = logging.getLogger(__name__)


class LFSTransferProtocol:
    """Line-delimited JSON protocol for a standalone Git LFS transfer agent."""

    def __init__(
        self,
        store_factory: Callable[[str, str], LFSObjectStore],
        stdin: TextIO,
        stdout: TextIO,
    ) -> None:
        self.store_factory = store_factory
        self.stdin = stdin
        self.stdout = stdout
        self._store: LFSObjectStore | None = None
        self.remote = ""
        self.operation: str | None = None

    def _write(self, message: dict) -> None:
        self.stdout.write(json.dumps(message) + "\n")
        self.stdout.flush()

    def _init(self, message: dict) -> None:
        if message.get("event") != "init":
            raise ProtocolError("expected LFS init message")
        operation = message.get("operation")
        if operation not in ("upload", "download"):
            raise ProtocolError("LFS operation must be upload or download")
        remote = message.get("remote")
        if not isinstance(remote, str) or not remote.strip():
            raise ProtocolError("LFS init requires a remote name or URL")
        self.remote = remote
        self.operation = operation
        self._write({})

    def _transfer(self, message: dict) -> None:
        oid = message.get("oid")
        response = {"event": "complete", "oid": oid if isinstance(oid, str) else ""}
        try:
            if message["event"] != self.operation:
                raise ProtocolError("LFS transfer does not match the initialized operation")
            if not isinstance(oid, str) or re.fullmatch(r"[0-9a-f]{64}", oid) is None:
                raise ProtocolError("LFS oid must be a lowercase SHA-256 hex digest")
            size = message.get("size")
            if type(size) is not int or size < 0:
                raise ProtocolError("LFS size must be a nonnegative integer")
            if self.operation == "upload":
                path = message.get("path")
                if not isinstance(path, str) or not path:
                    raise ProtocolError("LFS upload requires a source path")
            # OAuth and backend diagnostics must not enter the JSON stream.
            with redirect_stdout(sys.stderr):
                if self._store is None:
                    self._store = self.store_factory(self.remote, self.operation)
                if self.operation == "upload":
                    self._store.upload(oid, size, Path(path))
                else:
                    response["path"] = str(self._store.download(oid, size).resolve())
            self._write(
                {"event": "progress", "oid": oid, "bytesSoFar": size, "bytesSinceLast": size}
            )
        except Exception as exc:
            response["error"] = {"code": 2, "message": str(exc)}
            logger.error("LFS transfer failed for %s: %s", oid, exc)
        self._write(response)

    def run(self) -> int:
        try:
            for line in self.stdin:
                try:
                    message = json.loads(line)
                except ValueError as exc:
                    raise ProtocolError("invalid JSON in LFS request") from exc
                if not isinstance(message, dict):
                    raise ProtocolError("LFS request must be a JSON object")
                if self.operation is None:
                    self._init(message)
                    continue
                event = message.get("event")
                if event == "terminate":
                    return 0
                if event not in ("upload", "download"):
                    raise ProtocolError(f"unexpected LFS event: {event!r}")
                self._transfer(message)
            return 0
        except Exception as exc:
            logger.error("LFS protocol failed: %s", exc)
            if self.operation is None:
                self._write({"error": {"code": 1, "message": str(exc)}})
            return 1
        finally:
            if self._store is not None:
                try:
                    with redirect_stdout(sys.stderr):
                        self._store.close()
                except Exception as exc:
                    logger.warning("LFS temporary file cleanup failed: %s", exc)
