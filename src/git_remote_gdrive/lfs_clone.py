from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from .byte_progress import ByteProgressReporter


PROGRESS_ENV = "GDRIVE_LFS_CLONE_PROGRESS"
NATIVE_FILTER = "git-lfs filter-process"
CLONE_FILTER = "git-lfs-gdrive clone-filter"


def record_download_progress(oid: str, transferred: int, size: int) -> None:
    path = os.environ.get(PROGRESS_ENV)
    if path:
        try:
            with open(path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps([oid, transferred, size]) + "\n")
        except OSError:
            # A display failure must not turn a successful download into an error.
            pass


def _file_names() -> dict[str, str]:
    result = subprocess.run(
        ["git", "lfs", "ls-files", "--json", "HEAD"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    try:
        return {item["oid"]: item["name"] for item in json.loads(result.stdout)["files"]}
    except (ValueError, KeyError, TypeError):
        return {}


def clone_filter() -> int:
    """Observe this clone's downloads without touching the binary filter stream."""
    current = subprocess.run(
        ["git", "config", "--local", "--get", "filter.lfs.process"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    command = ["git", "lfs", "filter-process"]
    if current.stdout.strip() != CLONE_FILTER:
        return subprocess.call(command)
    # Restore before the first checkout, even if deferred with --no-checkout,
    # so download failure or interruption cannot leave our filter installed.
    subprocess.run(
        ["git", "config", "--local", "filter.lfs.process", NATIVE_FILTER],
        stdin=subprocess.DEVNULL, check=True,
    )

    names = _file_names()
    reporter = ByteProgressReporter(sys.stderr)
    with tempfile.TemporaryDirectory(prefix="git-lfs-gdrive-clone-") as directory:
        path = Path(directory) / "progress.jsonl"
        with path.open("a+b") as stream:
            environment = {**os.environ, PROGRESS_ENV: str(path)}
            # Git LFS owns stdin/stdout, including packet framing and file bytes.
            process = subprocess.Popen(command, env=environment)
            pending = b""

            def display_progress() -> None:
                nonlocal pending
                pending += stream.read()
                lines = pending.split(b"\n")
                pending = lines.pop()
                for line in lines:
                    oid, transferred, size = json.loads(line)
                    reporter.report("download", names.get(oid, oid[:12]), transferred, size)

            try:
                while True:
                    display_progress()
                    try:
                        result = process.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                display_progress()
                return result
            except KeyboardInterrupt:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                return 130
            finally:
                if process.poll() is None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                reporter.finish()
