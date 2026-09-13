from __future__ import annotations

import codecs
import os
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import BinaryIO, Sequence, TextIO

from .byte_progress import ByteProgressReporter


def push_with_progress(
    arguments: Sequence[str],
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run ordinary git push, displaying Git LFS's per-file byte counters."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    reporter = ByteProgressReporter(stderr)
    lock = threading.Lock()
    output_errors: list[Exception] = []

    def forward(pipe: BinaryIO, destination: TextIO) -> None:
        decoder = codecs.getincrementaldecoder(destination.encoding or "utf-8")("replace")
        try:
            while data := pipe.read1(8192):
                with lock:
                    reporter.finish()
                    if hasattr(destination, "buffer"):
                        destination.buffer.write(data)
                        destination.buffer.flush()
                    else:
                        destination.write(decoder.decode(data))
                        destination.flush()
        except Exception as exc:
            output_errors.append(exc)
        finally:
            pipe.close()

    with tempfile.TemporaryDirectory(prefix="git-lfs-gdrive-progress-") as directory:
        environment = os.environ.copy()
        progress_path = Path(
            environment.get("GIT_LFS_PROGRESS") or str(Path(directory) / "progress.log")
        )
        if not progress_path.is_absolute():
            raise ValueError("GIT_LFS_PROGRESS must be an absolute path")
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        environment["GIT_LFS_PROGRESS"] = str(progress_path)
        environment["GIT_LFS_FORCE_PROGRESS"] = "0"
        # Keep a caller's progress log intact and read only this push's updates.
        with progress_path.open("a+b") as progress_log:
            progress_log.seek(0, os.SEEK_END)
            pending = b""

            def read_progress() -> None:
                nonlocal pending
                pending += progress_log.read()
                lines = pending.split(b"\n")
                pending = lines.pop()
                with lock:
                    for line in lines:
                        reporter.update(line.decode("utf-8", "replace"))

            # Pipes disable the native intermediate object counter. Git output
            # and errors still reach their original streams; stdin stays usable.
            process = subprocess.Popen(
                ["git", "-c", "lfs.forceprogress=false", "push", *arguments],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert process.stdout is not None and process.stderr is not None
            readers = [
                threading.Thread(target=forward, args=(process.stdout, stdout), daemon=True),
                threading.Thread(target=forward, args=(process.stderr, stderr), daemon=True),
            ]
            for reader in readers:
                reader.start()
            try:
                while True:
                    read_progress()
                    if output_errors:
                        raise output_errors[0]
                    try:
                        result = process.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                for reader in readers:
                    reader.join()
                read_progress()
                if output_errors:
                    raise output_errors[0]
                return result
            except KeyboardInterrupt:
                # Inherit the terminal's process group so Ctrl+C reaches Git,
                # Git LFS and its agent, while retaining interactive Git prompts.
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                return 130
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                raise
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
                for reader in readers:
                    reader.join()
                with lock:
                    reporter.finish()
