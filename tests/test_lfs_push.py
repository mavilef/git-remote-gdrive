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


@unittest.skipUnless(os.name == "posix", "fake Git executable requires POSIX")
class TestLFSPush(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        self.environment.update({
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "TEST_ROOT": str(self.base),
            # These tests compare subprocess streams byte for byte. Ignore the
            # Google dependency's Python 3.10 EOL notice in the test fixture.
            "PYTHONWARNINGS": "ignore::FutureWarning:google.api_core._python_version_support",
        })
        self.command = [sys.executable, "-m", "git_remote_gdrive.lfs", "push"]

    def fake_git(self, body):
        executable = self.bin / "git"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, os, signal, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "root = Path(os.environ['TEST_ROOT'])\n"
            + textwrap.dedent(body),
            encoding="utf-8",
        )
        executable.chmod(0o755)

    def run_push(self, *arguments):
        return subprocess.run(
            [*self.command, *arguments], cwd=self.base, env=self.environment,
            capture_output=True, timeout=15, check=False,
        )

    def start_push(self):
        output = self.base / "stderr.log"
        stream = output.open("wb")
        self.addCleanup(stream.close)
        process = subprocess.Popen(
            self.command, cwd=self.base, env=self.environment,
            stdout=subprocess.PIPE, stderr=stream, start_new_session=True,
        )

        def cleanup():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            process.stdout.close()

        self.addCleanup(cleanup)
        return process, output

    def wait_until(self, predicate, process):
        deadline = time.monotonic() + 5
        while not predicate():
            self.assertIsNone(process.poll(), "push exited before the expected event")
            self.assertLess(time.monotonic(), deadline, "timed out waiting for push")
            time.sleep(0.02)

    def test_forwards_arguments_output_errors_and_exit_status(self):
        self.fake_git(r"""
            (root / 'invocation.json').write_text(json.dumps({
                'arguments': sys.argv[1:],
                'log': os.environ['GIT_LFS_PROGRESS'],
                'force': os.environ['GIT_LFS_FORCE_PROGRESS'],
            }))
            sys.stdout.buffer.write(b'push result \xff\n')
            sys.stderr.buffer.write(b'remote rejected: \xfe\n')
            sys.exit(7)
        """)
        arguments = [
            "--dry-run", "-u", "drive", "HEAD:refs/heads/main",
            "--push-option=hello world", "--help",
        ]

        result = self.run_push(*arguments)

        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"push result \xff\n")
        self.assertEqual(result.stderr, b"remote rejected: \xfe\n")
        invocation = json.loads((self.base / "invocation.json").read_text())
        actual = invocation["arguments"]
        self.assertEqual(actual[actual.index("push") + 1:], arguments)
        self.assertEqual(invocation["force"], "0")
        self.assertTrue(Path(invocation["log"]).is_absolute())
        self.assertFalse(Path(invocation["log"]).exists())

    def test_streams_fragmented_progress_before_exit_and_drains_final_record(self):
        self.fake_git(r"""
            def wait_for(name):
                deadline = time.monotonic() + 8
                while not (root / name).exists():
                    if time.monotonic() > deadline:
                        sys.exit(98)
                    time.sleep(0.02)

            with open(os.environ['GIT_LFS_PROGRESS'], 'ab', buffering=0) as log:
                log.write(b'upload 1/1 25/')
                (root / 'fragment').touch()
                wait_for('finish-line')
                log.write(b'100 asset with spaces.bin\n')
                wait_for('finish-push')
                log.write(b'upload 1/1 100/100 asset with spaces.bin\n')
        """)
        process, output = self.start_push()
        self.wait_until(lambda: (self.base / "fragment").exists(), process)
        time.sleep(0.2)
        self.assertEqual(output.read_bytes(), b"")

        (self.base / "finish-line").touch()
        self.wait_until(lambda: b"25.0%" in output.read_bytes(), process)
        self.assertIsNone(process.poll())
        self.assertIn(b"asset with spaces.bin", output.read_bytes())

        (self.base / "finish-push").touch()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertIn(b"100.0%", output.read_bytes())
        self.assertNotIn(b"1/1", output.read_bytes())

    def test_preserves_existing_progress_log_without_replaying_old_records(self):
        progress = self.base / "existing.log"
        previous = b"upload 1/1 100/100 old file.bin\n"
        current = b"upload 1/1 50/100 new file.bin\n"
        progress.write_bytes(previous)
        self.environment["GIT_LFS_PROGRESS"] = str(progress)
        self.fake_git(r"""
            with open(os.environ['GIT_LFS_PROGRESS'], 'ab') as log:
                log.write(b'upload 1/1 50/100 new file.bin\n')
        """)

        result = self.run_push()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(progress.read_bytes(), previous + current)
        self.assertIn(b"new file.bin: 50.0%", result.stderr)
        self.assertNotIn(b"old file.bin", result.stderr)

    def test_interrupt_stops_git_and_its_transfer_child(self):
        (self.base / "transfer.py").write_text(textwrap.dedent("""
            import signal, sys, time
            from pathlib import Path

            root = Path(sys.argv[1])
            def interrupted(signum, frame):
                (root / 'transfer-stopped').touch()
                sys.exit(0)

            signal.signal(signal.SIGINT, interrupted)
            (root / 'transfer-started').touch()
            while True:
                (root / 'transferred').write_text(str(time.monotonic_ns()))
                time.sleep(0.02)
        """), encoding="utf-8")
        self.fake_git("""
            child = subprocess.Popen([sys.executable, str(root / 'transfer.py'), str(root)])
            def interrupted(signum, frame):
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                (root / 'git-stopped').touch()
                child.wait(timeout=4)
                sys.exit(130)

            signal.signal(signal.SIGINT, interrupted)
            (root / 'git-started').touch()
            child.wait()
        """)
        process, _ = self.start_push()
        self.wait_until(
            lambda: all((self.base / name).exists() for name in (
                "git-started", "transfer-started", "transferred",
            )),
            process,
        )

        os.killpg(process.pid, signal.SIGINT)

        self.assertEqual(process.wait(timeout=8), 130)
        self.assertTrue((self.base / "git-stopped").exists())
        self.assertTrue((self.base / "transfer-stopped").exists())
        transferred = (self.base / "transferred").read_bytes()
        time.sleep(0.1)
        self.assertEqual((self.base / "transferred").read_bytes(), transferred)


if __name__ == "__main__":
    unittest.main()
