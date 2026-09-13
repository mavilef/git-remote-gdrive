import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from git_remote_gdrive.lfs_clone import CLONE_FILTER


@unittest.skipUnless(os.name == "posix", "fake Git executable requires POSIX")
class TestLFSCloneFilter(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.config = self.base / "filter-command"
        self.config.write_text(CLONE_FILTER)
        self.external_log = self.base / "caller-progress.log"
        self.external_log.write_bytes(b"existing progress\n")
        self.environment = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "TEST_ROOT": str(self.base),
            "GIT_LFS_PROGRESS": str(self.external_log),
        }
        self.environment.pop("GDRIVE_LFS_CLONE_PROGRESS", None)
        self.command = [sys.executable, "-m", "git_remote_gdrive.lfs", "clone-filter"]

    def fake_git(self, body):
        executable = self.bin / "git"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys, time\n"
            "from pathlib import Path\n"
            "root = Path(os.environ['TEST_ROOT'])\n"
            "if sys.argv[1:4] == ['config', '--local', '--get']:\n"
            "    print((root / 'filter-command').read_text()); sys.exit(0)\n"
            "if sys.argv[1:4] == ['config', '--local', 'filter.lfs.process']:\n"
            "    (root / 'filter-command').write_text(sys.argv[4]); sys.exit(0)\n"
            "if sys.argv[1:3] == ['lfs', 'ls-files']:\n"
            "    print(json.dumps({'files': [{'oid': 'a' * 64, 'name': 'large file.bin'}]})); sys.exit(0)\n"
            "assert sys.argv[1:] == ['lfs', 'filter-process']\n"
            + textwrap.dedent(body),
            encoding="utf-8",
        )
        executable.chmod(0o755)

    def test_reports_bytes_before_child_finishes_and_preserves_binary_streams(self):
        self.fake_git(r"""
            path = Path(os.environ['GDRIVE_LFS_CLONE_PROGRESS'])
            (root / 'environment.json').write_text(json.dumps(dict(os.environ)))
            sys.stdout.buffer.write(sys.stdin.buffer.read())
            sys.stdout.buffer.flush()
            with path.open('a') as log:
                record = json.dumps(['a' * 64, 4, 10]) + '\n'
                log.write(record[:12]); log.flush()
                time.sleep(0.15)
                log.write(record[12:]); log.flush()
                while not (root / 'release').exists():
                    time.sleep(0.01)
                log.write(json.dumps(['a' * 64, 10, 10]) + '\n')
        """)
        output = self.base / "stderr.log"
        with output.open("wb") as stream:
            process = subprocess.Popen(
                self.command, cwd=self.base, env=self.environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stream,
                start_new_session=True,
            )
            try:
                payload = b"000fcommand=smudge\x00\xff\n0000"
                process.stdin.write(payload)
                process.stdin.close()
                deadline = time.monotonic() + 8
                while b"40.0%" not in output.read_bytes():
                    self.assertIsNone(process.poll())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
                self.assertIsNone(process.poll(), "progress was buffered until completion")
                self.assertEqual(self.config.read_text(), "git-lfs filter-process")
                (self.base / "release").touch()
                self.assertEqual(process.wait(timeout=8), 0)
                self.assertEqual(process.stdout.read(), payload)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                process.stdout.close()

        self.assertIn(b"LFS download large file.bin: 100.0%", output.read_bytes())
        environment = json.loads((self.base / "environment.json").read_text())
        self.assertEqual(environment["GIT_LFS_PROGRESS"], str(self.external_log))
        self.assertEqual(self.external_log.read_bytes(), b"existing progress\n")
        self.assertFalse(Path(environment["GDRIVE_LFS_CLONE_PROGRESS"]).exists())

    def test_download_error_preserves_exit_status_output_and_restores_filter(self):
        self.fake_git(r"""
            path = Path(os.environ['GDRIVE_LFS_CLONE_PROGRESS'])
            path.write_text(json.dumps(['a' * 64, 4, 10]) + '\n')
            sys.stdout.buffer.write(b'filter bytes\x00\xff')
            sys.stderr.buffer.write(b'download failed\xfe\n')
            sys.exit(7)
        """)
        result = subprocess.run(
            self.command, cwd=self.base, env=self.environment,
            input=b"", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"filter bytes\x00\xff")
        self.assertIn(b"download failed\xfe\n", result.stderr)
        self.assertIn(b"40.0%", result.stderr)
        self.assertNotIn(b"100.0%", result.stderr)
        self.assertEqual(self.config.read_text(), "git-lfs filter-process")

    def test_preserves_a_filter_changed_after_clone_preparation(self):
        self.fake_git(r"""
            assert 'GDRIVE_LFS_CLONE_PROGRESS' not in os.environ
            sys.stdout.buffer.write(sys.stdin.buffer.read())
        """)
        self.config.write_text("custom-filter")
        result = subprocess.run(
            self.command, cwd=self.base, env=self.environment,
            input=b"binary\x00\xff", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"binary\x00\xff")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(self.config.read_text(), "custom-filter")
