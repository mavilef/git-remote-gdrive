import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestGitLFSEndToEnd(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.drive = self.base / "drive"
        (self.drive / "repo").mkdir(parents=True)
        self.environment = {
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "GDRIVE_LOCAL_ROOT": str(self.drive),
            "GDRIVE_CACHE_DIR": str(self.base / "source-cache"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_PUSH": "0",
            "GIT_LFS_SKIP_SMUDGE": "0",
        }
        if not shutil.which("git-lfs", path=self.environment["PATH"]):
            self.skipTest("Git LFS is not installed")
        self.source = self.base / "source"
        self.source.mkdir()
        self.git(self.source, "init", "--quiet", "--initial-branch", "main")
        self.git(self.source, "config", "user.name", "LFS Test")
        self.git(self.source, "config", "user.email", "test@example.com")
        self.git(self.source, "config", "lfs.transfer.maxretries", "1")
        self.git(self.source, "config", "lfs.transfer.maxretrydelay", "0")
        self.git(self.source, "remote", "add", "drive", "gd://repo")
        self.command(self.source, "git-lfs-gdrive", "install", "drive")
        self.git(self.source, "lfs", "track", "*.bin")

    def command(self, directory, *arguments, environment=None, expect_success=True):
        process = subprocess.run(
            arguments,
            cwd=directory,
            env=self.environment if environment is None else environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        if expect_success:
            self.assertEqual(
                process.returncode,
                0,
                f"{' '.join(arguments)} failed\n{process.stdout}\n{process.stderr}",
            )
        return process

    def git(self, directory, *arguments, **kwargs):
        return self.command(directory, "git", *arguments, **kwargs)

    def commit_binary(self, content, message):
        (self.source / "asset.bin").write_bytes(content)
        self.git(self.source, "add", ".gitattributes", "asset.bin")
        self.git(self.source, "commit", "--quiet", "--message", message)
        oid = hashlib.sha256(content).hexdigest()
        pointer = self.git(self.source, "show", "HEAD:asset.bin").stdout
        self.assertIn(f"oid sha256:{oid}\n", pointer)
        return oid

    def clone_drive(self):
        clone = self.base / "clone"
        self.environment["GDRIVE_CACHE_DIR"] = str(self.base / "clone-cache")
        environment = {
            **self.environment,
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
        self.git(
            self.base, "clone", "--quiet", "gd://repo", str(clone),
            environment=environment,
        )
        self.assertTrue(
            (clone / "asset.bin").read_bytes().startswith(
                b"version https://git-lfs.github.com/spec/v1\n"
            )
        )
        self.command(clone, "git-lfs-gdrive", "install", "origin")
        return clone

    def test_push_clone_pull_and_update_binary(self):
        first = bytes(range(256)) * 1024
        self.commit_binary(first, "first binary")
        self.git(self.source, "push", "--set-upstream", "drive", "main")

        clone = self.clone_drive()
        self.git(clone, "lfs", "pull", "origin")
        self.assertEqual((clone / "asset.bin").read_bytes(), first)

        second = first + b"updated\x00"
        self.commit_binary(second, "second binary")
        self.git(self.source, "push", "drive", "main")
        self.git(clone, "fetch", "origin")
        self.git(clone, "merge", "--ff-only", "origin/main")
        self.git(clone, "lfs", "pull", "origin")
        self.assertEqual((clone / "asset.bin").read_bytes(), second)
        self.git(clone, "lfs", "fsck")

    def test_restore_recovers_clone_with_unconfigured_global_lfs_filters(self):
        binary = b"recover failed checkout\x00" * 1024
        self.commit_binary(binary, "binary for checkout recovery")
        self.git(self.source, "push", "drive", "main")
        self.environment["GIT_CONFIG_GLOBAL"] = str(self.base / "global.gitconfig")
        self.git(self.source, "lfs", "install", "--skip-repo")
        self.environment["GDRIVE_CACHE_DIR"] = str(self.base / "clone-cache")
        clone = self.base / "failed-clone"

        result = self.git(
            self.base, "clone", "gd://repo", str(clone),
            environment={**self.environment, "GIT_SSH_COMMAND": "false"},
            expect_success=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Clone succeeded, but checkout failed", result.stderr)
        self.command(clone, "git-lfs-gdrive", "install", "origin")
        self.git(clone, "restore", "--source=HEAD", "--staged", "--worktree", ":/")
        self.assertEqual((clone / "asset.bin").read_bytes(), binary)
        self.assertEqual(self.git(clone, "status", "--porcelain").stdout, "")
        self.git(clone, "lfs", "fsck")

    def test_large_object_reports_progress_during_upload_and_download(self):
        binary = bytes(range(256)) * (64 * 1024) + b"last byte"
        self.commit_binary(binary, "binary spanning multiple transfer chunks")

        def assert_progress(path, direction):
            samples = [
                tuple(map(int, line.split()[2].split("/")))
                for line in path.read_text().splitlines()
                if line.startswith(direction + " ")
            ]
            self.assertTrue(samples, f"no {direction} progress reported")
            self.assertTrue(any(0 < done < total for done, total in samples))
            self.assertEqual(samples[-1], (len(binary), len(binary)))
            self.assertEqual(samples, sorted(samples))

        upload_log = self.base / "upload-progress.log"
        self.git(
            self.source, "push", "drive", "main",
            environment={**self.environment, "GIT_LFS_PROGRESS": str(upload_log)},
        )
        assert_progress(upload_log, "upload")

        retry_log = self.base / "retry-progress.log"
        self.git(
            self.source, "lfs", "push", "--all", "drive",
            environment={**self.environment, "GIT_LFS_PROGRESS": str(retry_log)},
        )
        assert_progress(retry_log, "upload")

        clone = self.clone_drive()
        download_log = self.base / "download-progress.log"
        self.git(
            clone, "lfs", "pull", "origin",
            environment={**self.environment, "GIT_LFS_PROGRESS": str(download_log)},
        )
        assert_progress(download_log, "download")
        self.assertEqual((clone / "asset.bin").read_bytes(), binary)

    def test_push_all_migrates_objects_from_previous_commits(self):
        first = b"historical\x00" * 1024
        self.commit_binary(first, "old binary")
        second = b"current\xff" * 1024
        self.commit_binary(second, "current binary")
        self.git(
            self.source, "push", "drive", "main",
            environment={**self.environment, "GIT_LFS_SKIP_PUSH": "1"},
        )
        objects = self.drive / "repo" / ".git-remote-gdrive" / "lfs" / "objects"
        self.assertFalse(objects.exists())

        self.git(self.source, "lfs", "push", "--all", "drive")
        clone = self.clone_drive()
        self.git(clone, "lfs", "fetch", "--all", "origin")
        self.git(clone, "lfs", "checkout")
        self.assertEqual((clone / "asset.bin").read_bytes(), second)
        self.git(clone, "checkout", "--quiet", "HEAD~1")
        self.assertEqual((clone / "asset.bin").read_bytes(), first)
        self.git(clone, "lfs", "fsck")

    def test_failed_upload_does_not_advance_git_refs(self):
        self.commit_binary(b"published\x00" * 1024, "published binary")
        self.git(self.source, "push", "drive", "main")
        before = self.git(self.source, "ls-remote", "drive", "refs/heads/main").stdout

        objects = self.drive / "repo" / ".git-remote-gdrive" / "lfs" / "objects"
        objects.rename(objects.with_name("saved-objects"))
        objects.write_text("uploads blocked", encoding="utf-8")
        self.commit_binary(b"unpublished\xff" * 1024, "unpublished binary")
        process = self.git(self.source, "push", "drive", "main", expect_success=False)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(
            self.git(self.source, "ls-remote", "drive", "refs/heads/main").stdout,
            before,
        )

    def test_install_keeps_other_lfs_remote_independent(self):
        other = self.base / "other.git"
        self.git(
            self.base, "init", "--bare", "--quiet", "--initial-branch", "main", str(other)
        )
        self.git(self.source, "remote", "add", "other", other.as_uri())
        self.command(self.source, "git-lfs-gdrive", "install", "drive")
        binary = b"other remote\x00" * 1024
        self.commit_binary(binary, "binary for other remote")
        self.git(self.source, "push", "other", "main")
        self.assertFalse((self.drive / "repo" / ".git-remote-gdrive").exists())

        clone = self.base / "other-clone"
        self.git(self.base, "clone", "--quiet", other.as_uri(), str(clone))
        self.git(clone, "lfs", "install", "--local")
        self.git(clone, "lfs", "pull", "origin")
        self.assertEqual((clone / "asset.bin").read_bytes(), binary)
        self.git(clone, "lfs", "fsck")

    def test_separate_fetch_and_push_urls_select_the_correct_folders(self):
        first = b"download from fetch URL\x00" * 1024
        first_oid = self.commit_binary(first, "downloadable binary")
        self.git(self.source, "push", "drive", "main")

        (self.drive / "uploads").mkdir()
        self.git(self.source, "remote", "set-url", "--push", "drive", "gd://uploads")
        self.command(self.source, "git-lfs-gdrive", "install", "drive")
        second_oid = self.commit_binary(b"push URL only\xff" * 1024, "upload binary")
        self.git(self.source, "lfs", "push", "--object-id", "drive", second_oid)
        upload_objects = self.drive / "uploads" / ".git-remote-gdrive" / "lfs" / "objects"
        fetch_objects = self.drive / "repo" / ".git-remote-gdrive" / "lfs" / "objects"
        self.assertTrue((upload_objects / second_oid[:2] / second_oid).is_file())
        self.assertFalse((fetch_objects / second_oid[:2] / second_oid).exists())
        self.assertFalse((upload_objects / first_oid[:2] / first_oid).exists())

        clone = self.clone_drive()
        self.git(clone, "remote", "set-url", "--push", "origin", "gd://uploads")
        self.command(clone, "git-lfs-gdrive", "install", "origin")
        self.git(clone, "lfs", "pull", "origin")
        self.assertEqual((clone / "asset.bin").read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
