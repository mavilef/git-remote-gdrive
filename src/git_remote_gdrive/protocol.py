from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TextIO

from .errors import GitRemoteGDriveError, ProtocolError
from .remote import FetchRequest, GitDriveRemote, PushRequest


logger = logging.getLogger(__name__)


def _protocol_message(message: str) -> str:
    return " ".join(message.splitlines()).strip()


class RemoteHelperProtocol:
    def __init__(
        self,
        remote_factory: Callable[[], GitDriveRemote],
        stdin: TextIO,
        stdout: TextIO,
    ) -> None:
        self.remote_factory = remote_factory
        self.stdin = stdin
        self.stdout = stdout
        self._remote: GitDriveRemote | None = None
        self.options = {
            "verbosity": 1,
            "progress": True,
            "dry-run": False,
            "force": False,
            "cloning": False,
            "check-connectivity": False,
        }

    @property
    def remote(self) -> GitDriveRemote:
        if self._remote is None:
            self._remote = self.remote_factory()
        return self._remote

    def _write_line(self, value: str = "") -> None:
        self.stdout.write(value + "\n")
        self.stdout.flush()

    def _read_batch(self, first_command: str, prefix: str) -> list[str]:
        commands = [first_command]
        while True:
            raw = self.stdin.readline()
            if raw == "":
                raise ProtocolError(f"unexpected EOF in {prefix} command batch")
            command = raw.rstrip("\r\n")
            if not command:
                return commands
            if not command.startswith(prefix):
                raise ProtocolError(
                    f"unexpected command in {prefix.strip()} batch: {command!r}"
                )
            commands.append(command)

    def _capabilities(self) -> None:
        self._write_line("fetch")
        self._write_line("push")
        self._write_line("option")
        self._write_line()

    @staticmethod
    def _parse_bool(value: str) -> bool | None:
        if value == "true":
            return True
        if value == "false":
            return False
        return None

    def _option(self, command: str) -> None:
        parts = command.split(" ", 2)
        if len(parts) != 3:
            self._write_line("error malformed option")
            return
        _, name, value = parts

        if name == "verbosity":
            try:
                verbosity = int(value)
            except ValueError:
                self._write_line("error invalid verbosity")
                return
            if verbosity < 0:
                self._write_line("error invalid verbosity")
                return
            self.options[name] = verbosity
            self._write_line("ok")
            return

        if name in {
            "progress",
            "dry-run",
            "force",
            "cloning",
            "check-connectivity",
        }:
            parsed = self._parse_bool(value)
            if parsed is None:
                self._write_line(f"error invalid value for {name}")
                return
            self.options[name] = parsed
            self._write_line("ok")
            return

        self._write_line("unsupported")

    def _list(self) -> None:
        for line in self.remote.list_refs():
            self._write_line(line)
        self._write_line()

    def _fetch(self, first_command: str) -> None:
        commands = self._read_batch(first_command, "fetch ")
        requests: list[FetchRequest] = []
        for command in commands:
            parts = command.split(" ", 2)
            if len(parts) != 3 or not parts[1] or not parts[2]:
                raise ProtocolError(f"malformed fetch command: {command!r}")
            requests.append(FetchRequest(parts[1], parts[2]))

        self.remote.fetch(
            requests,
            check_connectivity=bool(self.options["check-connectivity"]),
        )
        if self.options["check-connectivity"]:
            self._write_line("connectivity-ok")
        self._write_line()

    def _push(self, first_command: str) -> None:
        commands = self._read_batch(first_command, "push ")
        requests = [PushRequest.parse(command) for command in commands]
        try:
            results = self.remote.push(
                requests,
                dry_run=bool(self.options["dry-run"]),
                force_option=bool(self.options["force"]),
            )
        except GitRemoteGDriveError as exc:
            message = _protocol_message(str(exc))
            for request in requests:
                self._write_line(f"error {request.destination} {message}")
            self._write_line()
            logger.error("push failed: %s", exc)
            return

        for result in results:
            if result.ok:
                self._write_line(f"ok {result.destination}")
            else:
                message = _protocol_message(result.message or "push rejected")
                self._write_line(f"error {result.destination} {message}")
        self._write_line()

    def run(self) -> int:
        try:
            while True:
                raw = self.stdin.readline()
                if raw == "":
                    return 0
                command = raw.rstrip("\r\n")
                if not command:
                    return 0
                if command == "capabilities":
                    self._capabilities()
                elif command.startswith("option "):
                    self._option(command)
                elif command in {"list", "list for-push"}:
                    self._list()
                elif command.startswith("fetch "):
                    self._fetch(command)
                elif command.startswith("push "):
                    self._push(command)
                else:
                    raise ProtocolError(f"unsupported helper command: {command!r}")
        except GitRemoteGDriveError as exc:
            logger.error("%s", exc)
            return 1
        except Exception as exc:  # Keep tracebacks away from the helper protocol.
            logger.error("unexpected failure: %s", exc)
            logger.debug("unexpected helper failure", exc_info=True)
            return 1
