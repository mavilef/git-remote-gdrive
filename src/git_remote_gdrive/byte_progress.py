from __future__ import annotations

import re
from typing import TextIO


def _format_bytes(size: int) -> str:
    divisor = 1
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"):
        if size < divisor * 1024 or unit == "EiB":
            tenths = (size * 10 + divisor // 2) // divisor
            return f"{tenths // 10}.{tenths % 10} {unit}"
        divisor *= 1024
    raise AssertionError("unreachable")


class ByteProgressReporter:
    """Render Git LFS progress records as the current file's byte percentage."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self._tty = stream.isatty()
        self._line_width = 0
        self._last_key: tuple[str, str, int] | None = None
        self._last_bytes = 0
        self._last_percent = -1

    def update(self, line: str) -> None:
        fields = line.rstrip("\r\n").split(maxsplit=3)
        if len(fields) != 4:
            return
        direction, file_counts, byte_counts, name = fields
        if direction not in ("upload", "download", "checkout"):
            return
        if not all(re.fullmatch(r"[0-9]+/[0-9]+", value) for value in (file_counts, byte_counts)):
            return
        try:
            transferred, size = map(int, byte_counts.split("/"))
        except ValueError:
            return
        if transferred > size:
            return

        percent = transferred * 100 // size if size else 100
        key = (direction, name, size)
        repeated = (
            key == self._last_key
            and percent == self._last_percent
            and transferred >= self._last_bytes
            and transferred != size
        )
        self._last_key = key
        self._last_percent = percent
        self._last_bytes = transferred
        if repeated:
            return

        name = "".join(character if character.isprintable() else "?" for character in name)
        tenths = transferred * 1000 // size if size else 1000
        message = (
            f"LFS {direction} {name}: {tenths // 10}.{tenths % 10}% "
            f"({_format_bytes(transferred)} / {_format_bytes(size)})"
        )
        if self._tty:
            self.stream.write("\r" + message + " " * max(0, self._line_width - len(message)))
            self._line_width = len(message)
        else:
            self.stream.write(message + "\n")
        self.stream.flush()

    def finish(self) -> None:
        if self._line_width:
            self.stream.write("\n")
            self.stream.flush()
            self._line_width = 0
