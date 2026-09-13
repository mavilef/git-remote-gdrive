import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def run(
    directory: Path,
    environment: dict[str, str],
    *arguments: str,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", *arguments],
        cwd=directory,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if expect_success and process.returncode:
        raise AssertionError(
            f"git {' '.join(arguments)} failed\nstdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    return process


class TestGitEndToEnd(unittest.TestCase):
    def test_push_clone_fetch_and_tag(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            drive = base / "drive"
            (drive / "repo").mkdir(parents=True)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{Path(sys.executable).parent}{os.pathsep}{environment['PATH']}",
                    "GDRIVE_LOCAL_ROOT": str(drive),
                    "GDRIVE_CACHE_DIR": str(base / "cache"),
                }
            )

            source = base / "source"
            source.mkdir()
            run(source, environment, "init", "--quiet", "--initial-branch", "main")
            run(source, environment, "config", "user.name", "Test User")
            run(source, environment, "config", "user.email", "test@example.com")
            (source / "message.txt").write_text("first\n", encoding="utf-8")
            run(source, environment, "add", "message.txt")
            run(source, environment, "commit", "--quiet", "--message", "first")
            run(source, environment, "remote", "add", "drive", "gd://repo")
            run(source, environment, "push", "--set-upstream", "drive", "main")

            clone = base / "clone"
            run(base, environment, "clone", "--quiet", "gd://repo", str(clone))
            self.assertEqual((clone / "message.txt").read_text(), "first\n")

            (source / "message.txt").write_text("second\n", encoding="utf-8")
            run(source, environment, "commit", "--all", "--quiet", "--message", "second")
            run(source, environment, "tag", "v1")
            run(source, environment, "push", "drive", "main", "v1")

            run(clone, environment, "fetch", "--tags", "origin")
            run(clone, environment, "merge", "--ff-only", "origin/main")
            self.assertEqual((clone / "message.txt").read_text(), "second\n")
            tag = run(clone, environment, "rev-parse", "v1").stdout.strip()
            head = run(clone, environment, "rev-parse", "HEAD").stdout.strip()
            self.assertEqual(tag, head)

            run(source, environment, "branch", "temporary")
            run(source, environment, "push", "drive", "temporary")
            listed = run(source, environment, "ls-remote", "--heads", "drive")
            self.assertIn("refs/heads/temporary", listed.stdout)

            run(source, environment, "push", "drive", "--delete", "temporary")
            listed = run(source, environment, "ls-remote", "--heads", "drive")
            self.assertNotIn("refs/heads/temporary", listed.stdout)

            run(source, environment, "push", "--dry-run", "drive", "temporary")
            listed = run(source, environment, "ls-remote", "--heads", "drive")
            self.assertNotIn("refs/heads/temporary", listed.stdout)


if __name__ == "__main__":
    unittest.main()
