import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git_remote_gdrive.errors import DriveError
from git_remote_gdrive.git_repository import GitRepository
from git_remote_gdrive.local_drive_client import LocalDriveClient
from git_remote_gdrive.remote import FetchRequest, GitDriveRemote, PushRequest
from git_remote_gdrive.store import DriveRemoteStore


def git(directory: Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", *arguments],
        cwd=directory,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise AssertionError(process.stderr or process.stdout)
    return process.stdout.strip()


def initialize_repository(path: Path, *, bare: bool = False) -> None:
    path.mkdir()
    arguments = ["init", "--quiet"]
    if bare:
        arguments.append("--bare")
    else:
        arguments.extend(["--initial-branch", "main"])
    git(path, *arguments)
    if not bare:
        git(path, "config", "user.name", "Test User")
        git(path, "config", "user.email", "test@example.com")


class TestGitDriveRemote(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.drive = self.base / "drive"
        self.remote_root = self.drive / "remote"
        self.remote_root.mkdir(parents=True)
        self.cache = self.base / "cache"

    def tearDown(self):
        self.temporary.cleanup()

    def remote_for(self, repository: Path) -> GitDriveRemote:
        store = DriveRemoteStore(
            LocalDriveClient(self.drive), "remote", cache_dir=self.cache
        )
        return GitDriveRemote(store, GitRepository(repository))

    def commit(self, repository: Path, message: str, content: str) -> str:
        (repository / "file.txt").write_text(content, encoding="utf-8")
        git(repository, "add", "file.txt")
        git(repository, "commit", "--quiet", "--message", message)
        return git(repository, "rev-parse", "HEAD")

    def test_incremental_push_and_fetch(self):
        source = self.base / "source"
        initialize_repository(source)
        first = self.commit(source, "first", "one\n")
        source_remote = self.remote_for(source)

        result = source_remote.push(
            [PushRequest("refs/heads/main", "refs/heads/main")],
            dry_run=False,
            force_option=False,
        )
        self.assertTrue(result[0].ok)

        target = self.base / "target.git"
        initialize_repository(target, bare=True)
        target_remote = self.remote_for(target)
        target_remote.fetch(
            [FetchRequest(first, "refs/heads/main")], check_connectivity=True
        )
        self.assertTrue(GitRepository(target).has_object(first))

        second = self.commit(source, "second", "two\n")
        result = source_remote.push(
            [PushRequest("refs/heads/main", "refs/heads/main")],
            dry_run=False,
            force_option=False,
        )
        self.assertTrue(result[0].ok)

        manifest = DriveRemoteStore(
            LocalDriveClient(self.drive), "remote", cache_dir=self.cache
        ).load_manifest().manifest
        self.assertEqual(manifest.generation, 2)
        self.assertEqual(manifest.refs["refs/heads/main"], second)
        self.assertEqual(len(manifest.bundles), 2)
        self.assertIn(first, manifest.bundles[1].prerequisites)

        target_remote = self.remote_for(target)
        target_remote.fetch(
            [FetchRequest(second, "refs/heads/main")], check_connectivity=True
        )
        self.assertTrue(GitRepository(target).has_object(second))

    def test_lost_manifest_response_preserves_remote_history(self):
        source = self.base / "source"
        initialize_repository(source)
        self.commit(source, "first", "one\n")
        remote = self.remote_for(source)
        requests = [PushRequest("refs/heads/main", "refs/heads/main")]
        remote.push(requests, dry_run=False, force_option=False)
        second = self.commit(source, "second", "two\n")

        upload_bytes = remote.store.client.upload_bytes

        def upload_then_fail(*args, **kwargs):
            upload_bytes(*args, **kwargs)
            raise DriveError("response lost after manifest upload")

        with patch.object(remote.store.client, "upload_bytes", side_effect=upload_then_fail):
            with self.assertRaisesRegex(DriveError, "response lost"):
                remote.push(requests, dry_run=False, force_option=False)

        target = self.base / "target.git"
        initialize_repository(target, bare=True)
        target_remote = self.remote_for(target)
        manifest = target_remote.store.load_manifest().manifest
        self.assertEqual(manifest.refs["refs/heads/main"], second)
        target_remote.fetch(
            [FetchRequest(second, "refs/heads/main")], check_connectivity=True
        )
        self.assertEqual(git(target, "show", f"{second}:file.txt"), "two")

    def test_non_fast_forward_is_rejected_unless_forced(self):
        source = self.base / "source"
        initialize_repository(source)
        self.commit(source, "first", "one\n")
        remote = self.remote_for(source)
        remote.push(
            [PushRequest("refs/heads/main", "refs/heads/main")],
            dry_run=False,
            force_option=False,
        )

        git(source, "checkout", "--quiet", "--orphan", "replacement")
        git(source, "rm", "--quiet", "--cached", "file.txt")
        self.commit(source, "replacement", "other\n")

        rejected = remote.push(
            [PushRequest("refs/heads/replacement", "refs/heads/main")],
            dry_run=False,
            force_option=False,
        )
        self.assertFalse(rejected[0].ok)
        self.assertEqual(rejected[0].message, "non-fast-forward")

        accepted = remote.push(
            [PushRequest("refs/heads/replacement", "refs/heads/main", force=True)],
            dry_run=False,
            force_option=False,
        )
        self.assertTrue(accepted[0].ok)


if __name__ == "__main__":
    unittest.main()
