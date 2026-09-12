"""Opt-in integration test; GDRIVE_TEST_FOLDER_ID selects a disposable parent."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


@unittest.skipUnless(
    os.environ.get("GDRIVE_TEST_FOLDER_ID"),
    "set GDRIVE_TEST_FOLDER_ID to enable the live Google Drive test",
)
class TestLiveGoogleDrive(unittest.TestCase):
    def git(self, directory: Path, *arguments: str) -> str:
        process = subprocess.run(
            ["git", *arguments],
            cwd=directory,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
            check=False,
        )
        self.assertEqual(
            process.returncode,
            0,
            f"git {' '.join(arguments)} failed\n{process.stdout}\n{process.stderr}",
        )
        return process.stdout.strip()

    def test_push_clone_fetch_tags_delete_and_dry_run(self):
        from git_remote_gdrive.google_drive_client_impl import GoogleDriveClientImpl

        credentials_path = os.environ.get("GDRIVE_CREDENTIALS_PATH")
        self.assertTrue(credentials_path, "GDRIVE_CREDENTIALS_PATH must be set")
        self.environment = os.environ.copy()
        self.environment.pop("GDRIVE_LOCAL_ROOT", None)
        self.environment.update(
            {
                "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            }
        )
        self.assertIsNotNone(
            shutil.which("git-remote-gd", path=self.environment["PATH"]),
            "install the project with uv sync before running the live test",
        )

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.environment["GDRIVE_CACHE_DIR"] = str(base / "source-cache")
        client = GoogleDriveClientImpl(
            credentials_path,
            token_path=os.environ.get("GDRIVE_TOKEN_PATH"),
        )
        folder = client.create_folder(
            os.environ["GDRIVE_TEST_FOLDER_ID"],
            f"git-remote-gdrive-test-{uuid.uuid4().hex}",
        )
        self.addCleanup(client.delete_file, folder.id)
        remote_url = f"gd://{folder.id}"

        source = base / "source"
        source.mkdir()
        self.git(source, "init", "--quiet", "--initial-branch", "main")
        self.git(source, "config", "user.name", "Live Test")
        self.git(source, "config", "user.email", "test@example.com")
        (source / "message.txt").write_text("first\n", encoding="utf-8")
        self.git(source, "add", "message.txt")
        self.git(source, "commit", "--quiet", "--message", "first")
        self.git(source, "remote", "add", "drive", remote_url)
        self.git(source, "push", "--set-upstream", "drive", "main")

        clone = base / "clone"
        # A fresh cache makes clone download the published bundle from Drive.
        self.environment["GDRIVE_CACHE_DIR"] = str(base / "clone-cache")
        self.git(base, "clone", "--quiet", remote_url, str(clone))
        self.assertEqual((clone / "message.txt").read_text(), "first\n")

        self.environment["GDRIVE_CACHE_DIR"] = str(base / "source-cache")
        (source / "message.txt").write_text("second\n", encoding="utf-8")
        self.git(source, "commit", "--all", "--quiet", "--message", "second")
        self.git(source, "tag", "v1")
        self.git(source, "push", "drive", "main", "v1")

        self.environment["GDRIVE_CACHE_DIR"] = str(base / "clone-cache")
        self.git(clone, "fetch", "--tags", "origin")
        self.git(clone, "merge", "--ff-only", "origin/main")
        self.assertEqual((clone / "message.txt").read_text(), "second\n")
        self.assertEqual(
            self.git(clone, "rev-parse", "v1"),
            self.git(source, "rev-parse", "HEAD"),
        )

        self.environment["GDRIVE_CACHE_DIR"] = str(base / "source-cache")
        self.git(source, "branch", "temporary")
        self.git(source, "push", "drive", "temporary")
        self.assertIn(
            "refs/heads/temporary", self.git(source, "ls-remote", "--heads", "drive")
        )
        self.git(source, "push", "drive", "--delete", "temporary")
        self.assertNotIn(
            "refs/heads/temporary", self.git(source, "ls-remote", "--heads", "drive")
        )
        self.git(source, "push", "--dry-run", "drive", "temporary")
        self.assertNotIn(
            "refs/heads/temporary", self.git(source, "ls-remote", "--heads", "drive")
        )


if __name__ == "__main__":
    unittest.main()
