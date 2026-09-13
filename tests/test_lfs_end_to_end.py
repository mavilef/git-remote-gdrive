import hashlib
import os
import re
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
        self.git(self.source, "lfs", "install", "--local")
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

    def test_clone_checks_out_lfs_without_manual_setup(self):
        binary = bytes(range(256)) * 1024 + b"complete checkout\x00"
        oid = self.commit_binary(binary, "binary for automatic clone")
        self.git(self.source, "push", "drive", "main")

        for global_filters, remote in ((False, "origin"), (True, "cloud")):
            with self.subTest(global_filters=global_filters, remote=remote):
                clone = self.base / f"clone-{remote}"
                environment = {
                    **self.environment,
                    "GDRIVE_CACHE_DIR": str(self.base / f"cache-{remote}"),
                    "GIT_SSH_COMMAND": "false",
                }
                if global_filters:
                    environment["GIT_CONFIG_GLOBAL"] = str(self.base / "global.gitconfig")
                    self.git(
                        self.source, "lfs", "install", "--skip-repo",
                        environment=environment,
                    )

                self.git(
                    self.base, "clone", "--origin", remote, "gd://repo", str(clone),
                    environment=environment,
                )

                actual = (clone / "asset.bin").read_bytes()
                self.assertEqual(len(actual), len(binary))
                self.assertEqual(hashlib.sha256(actual).hexdigest(), oid)
                self.assertEqual(self.git(clone, "status", "--porcelain").stdout, "")
                self.git(clone, "lfs", "fsck", environment=environment)
                hook = clone / ".git/hooks/pre-push"
                if hook.exists():
                    self.assertNotIn(b"git-lfs-gdrive", hook.read_bytes())

    def test_clone_tag_ignores_lfsconfig_on_another_fetched_branch(self):
        binary = b"LFS from the selected tag\x00" * 1024
        self.commit_binary(binary, "binary without an endpoint override")
        self.git(self.source, "tag", "--annotate", "clean-v1", "--message", "clean release")
        endpoint = "https://lfs.example.invalid/repository"
        self.git(self.source, "config", "--file", ".lfsconfig", "lfs.url", endpoint)
        self.git(self.source, "add", ".lfsconfig")
        self.git(self.source, "commit", "--quiet", "-m", "external LFS endpoint on main")
        self.git(self.source, "checkout", "--quiet", "clean-v1")
        self.git(self.source, "push", "drive", "main", "refs/tags/clean-v1")
        clone = self.base / "tag-clone"
        environment = {
            **self.environment,
            "GDRIVE_CACHE_DIR": str(self.base / "tag-clone-cache"),
            "GIT_SSH_COMMAND": "false",
        }

        self.git(
            self.base, "clone", "--branch", "clean-v1", "gd://repo", str(clone),
            environment=environment,
        )

        self.assertEqual((clone / "asset.bin").read_bytes(), binary)
        self.assertFalse((clone / ".lfsconfig").exists())
        self.assertIn(endpoint, self.git(clone, "show", "origin/main:.lfsconfig").stdout)
        self.assertEqual(self.git(clone, "status", "--porcelain").stdout, "")
        self.git(clone, "lfs", "fsck", environment=environment)

    def test_clone_without_checkout_prepares_lfs_for_later_download(self):
        binary = b"deferred LFS download\x00" * 1024
        oid = self.commit_binary(binary, "binary for deferred checkout")
        self.git(self.source, "push", "drive", "main")

        for mode in ("--no-checkout", "--bare"):
            with self.subTest(mode=mode):
                clone = self.base / mode.removeprefix("--")
                environment = {
                    **self.environment,
                    "GDRIVE_CACHE_DIR": str(self.base / f"cache-{clone.name}"),
                    "GIT_SSH_COMMAND": "false",
                }
                self.git(
                    self.base, "clone", mode, "gd://repo", str(clone),
                    environment=environment,
                )
                self.assertFalse((clone / "asset.bin").exists())
                self.assertIn(f"oid sha256:{oid}", self.git(clone, "show", "HEAD:asset.bin").stdout)
                self.git(clone, "lfs", "fetch", "origin", environment=environment)
                git_dir = clone if mode == "--bare" else clone / ".git"
                local_object = git_dir / "lfs/objects" / oid[:2] / oid[2:4] / oid
                self.assertEqual(local_object.read_bytes(), binary)
                if mode == "--no-checkout":
                    self.git(clone, "reset", "--hard", "HEAD", environment=environment)
                    self.assertEqual((clone / "asset.bin").read_bytes(), binary)

    def test_restore_recovers_clone_after_a_missing_lfs_object_is_restored(self):
        binary = b"recover failed checkout\x00" * 1024
        oid = self.commit_binary(binary, "binary for checkout recovery")
        self.git(self.source, "push", "drive", "main")
        remote_object = (
            self.drive / "repo/.git-remote-gdrive/lfs/objects" / oid[:2] / oid
        )
        saved_object = self.base / "saved-object"
        remote_object.rename(saved_object)
        self.environment["GDRIVE_CACHE_DIR"] = str(self.base / "clone-cache")
        clone = self.base / "failed-clone"

        result = self.git(
            self.base, "-c", "lfs.transfer.maxretries=1", "-c", "lfs.transfer.maxretrydelay=0",
            "clone", "gd://repo", str(clone), expect_success=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Clone succeeded, but checkout failed", result.stderr)
        saved_object.rename(remote_object)
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

        self.command(self.source, "git-lfs-gdrive", "install", "drive")
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

        self.command(self.source, "git-lfs-gdrive", "install", "drive")
        self.git(self.source, "lfs", "push", "--all", "drive")
        clone = self.clone_drive()
        self.git(clone, "lfs", "fetch", "--all", "origin")
        self.git(clone, "lfs", "checkout")
        self.assertEqual((clone / "asset.bin").read_bytes(), second)
        self.git(clone, "checkout", "--quiet", "HEAD~1")
        self.assertEqual((clone / "asset.bin").read_bytes(), first)
        self.git(clone, "lfs", "fsck")

    def test_git_push_reports_byte_percentage_and_sets_upstream(self):
        binary = bytes(range(256)) * (64 * 1024) + b"last byte"
        self.commit_binary(binary, "binary with visible byte progress")
        hook = self.source / ".git/hooks/pre-push"
        self.assertNotIn(b"git-lfs-gdrive", hook.read_bytes())

        process = self.git(self.source, "push", "-u", "drive", "main")

        self.assertIn(b"git-lfs-gdrive pre-push", hook.read_bytes())
        endpoints = self.git(
            self.source, "config", "--local", "--get-regexp", r"\.(lfsurl|lfspushurl)$",
            expect_success=False,
        )
        self.assertEqual(endpoints.stdout, "")
        percentages = [
            float(value)
            for value in re.findall(
                r"LFS upload asset\.bin: (\d+(?:\.\d+)?)% \(", process.stderr
            )
        ]
        self.assertTrue(
            any(0 < value < 100 for value in percentages), process.stderr
        )
        self.assertEqual(percentages[-1], 100)
        self.assertIn("MiB / 16.0 MiB)", process.stderr)
        self.assertIn("[new branch]", process.stderr)
        self.assertIn("drive/main", process.stdout)
        self.assertEqual(
            self.git(
                self.source, "rev-parse", "--symbolic-full-name", "@{upstream}"
            ).stdout.strip(),
            "refs/remotes/drive/main",
        )
        head = self.git(self.source, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(
            self.git(self.source, "ls-remote", "drive", "refs/heads/main").stdout,
            f"{head}\trefs/heads/main\n",
        )
        clone = self.clone_drive()
        self.git(clone, "lfs", "pull", "origin")
        self.assertEqual((clone / "asset.bin").read_bytes(), binary)
        self.git(clone, "lfs", "fsck")

    def test_direct_url_push_installs_a_missing_hook_without_creating_remotes(self):
        binary = b"direct URL upload\x00" * 1024
        oid = self.commit_binary(binary, "binary without pre-push hook")
        hook = self.source / ".git/hooks/pre-push"
        hook.unlink()
        self.git(self.source, "remote", "remove", "drive")
        (self.drive / "explicit").mkdir()

        for url, folder in (("gd://repo", "repo"), ("gdrive::explicit", "explicit")):
            with self.subTest(url=url):
                result = self.git(self.source, "push", url, "main")
                self.assertIn("LFS upload asset.bin:", result.stderr)
                remote_object = (
                    self.drive / folder / ".git-remote-gdrive" / "lfs" / "objects"
                    / oid[:2] / oid
                )
                self.assertEqual(remote_object.read_bytes(), binary)
        self.assertIn(b"git-lfs-gdrive pre-push", hook.read_bytes())
        self.assertEqual(self.git(self.source, "remote").stdout, "")

    def test_auto_setup_finds_lfs_on_pushed_branch_when_head_has_no_pointers(self):
        binary = b"LFS on another branch\x00" * 1024
        oid = self.commit_binary(binary, "binary on main")
        main = self.git(self.source, "rev-parse", "main").stdout.strip()
        self.git(self.source, "checkout", "--orphan", "plain")
        self.git(self.source, "rm", "-rf", ".")
        (self.source / "plain.txt").write_text("No LFS files here.\n", encoding="utf-8")
        self.git(self.source, "add", "plain.txt")
        self.git(self.source, "commit", "--quiet", "-m", "plain branch")
        self.assertEqual(self.git(self.source, "lfs", "ls-files").stdout, "")
        hook = self.source / ".git/hooks/pre-push"
        hook.unlink()

        result = self.git(self.source, "push", "drive", "main")

        self.assertIn("LFS upload asset.bin:", result.stderr)
        self.assertIn(b"git-lfs-gdrive pre-push", hook.read_bytes())
        remote_object = (
            self.drive / "repo" / ".git-remote-gdrive" / "lfs" / "objects" / oid[:2] / oid
        )
        self.assertEqual(remote_object.read_bytes(), binary)
        self.assertEqual(
            self.git(self.source, "ls-remote", "drive", "refs/heads/main").stdout,
            f"{main}\trefs/heads/main\n",
        )

    def test_auto_setup_preserves_external_fetch_endpoint_with_drive_push_url(self):
        binary = b"mixed remote upload\x00" * 1024
        oid = self.commit_binary(binary, "binary for Drive push URL")
        fetch_url = "https://example.invalid/repository.git"
        lfs_url = "https://lfs.example.invalid/repository"
        self.git(self.source, "remote", "set-url", "drive", fetch_url)
        self.git(self.source, "remote", "set-url", "--push", "drive", "gd://repo")
        self.git(self.source, "config", "remote.drive.lfsurl", lfs_url)

        result = self.git(self.source, "push", "drive", "main")

        self.assertIn("LFS upload asset.bin:", result.stderr)
        self.assertEqual(
            self.git(self.source, "remote", "get-url", "drive").stdout.strip(), fetch_url,
        )
        self.assertEqual(
            self.git(self.source, "config", "--get", "remote.drive.lfsurl").stdout.strip(),
            lfs_url,
        )
        self.assertNotEqual(self.git(
            self.source, "config", "--get", "remote.drive.lfspushurl", expect_success=False,
        ).returncode, 0)
        remote_object = (
            self.drive / "repo" / ".git-remote-gdrive" / "lfs" / "objects" / oid[:2] / oid
        )
        self.assertEqual(remote_object.read_bytes(), binary)

    def test_read_operations_do_not_install_or_configure_drive_lfs(self):
        self.commit_binary(b"local binary\x00" * 1024, "unpublished binary")
        configuration = (self.source / ".git/config").read_bytes()
        hook = (self.source / ".git/hooks/pre-push").read_bytes()

        self.git(self.source, "ls-remote", "drive")
        self.git(self.source, "fetch", "drive")
        clone = self.base / "read-clone"
        self.git(self.base, "clone", "--no-checkout", "gd://repo", str(clone))

        self.assertEqual((self.source / ".git/config").read_bytes(), configuration)
        self.assertEqual((self.source / ".git/hooks/pre-push").read_bytes(), hook)
        self.assertFalse((clone / ".git/hooks/pre-push").exists())
        self.assertNotIn(b"git-remote-gdrive.invalid", (clone / ".git/config").read_bytes())

    def test_auto_setup_preserves_custom_hook_and_rejects_lfs_push(self):
        self.commit_binary(b"custom hook binary\x00" * 1024, "unpublished binary")
        hook = self.source / ".git/hooks/pre-push"
        content = b"#!/bin/sh\nprintf 'custom hook\\n'\n"
        hook.write_bytes(content)
        configuration = (self.source / ".git/config").read_bytes()

        result = self.git(self.source, "push", "drive", "main", expect_success=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Hook already exists", result.stderr)
        self.assertEqual(hook.read_bytes(), content)
        self.assertEqual((self.source / ".git/config").read_bytes(), configuration)
        self.assertEqual(self.git(self.source, "ls-remote", "drive").stdout, "")

    def test_auto_setup_preserves_conflicting_endpoint_overrides(self):
        self.commit_binary(b"endpoint override binary\x00" * 1024, "unpublished binary")
        hook = (self.source / ".git/hooks/pre-push").read_bytes()
        endpoint = "https://lfs.example.invalid/repository"
        for key in ("lfs.url", "lfs.pushurl", "remote.drive.lfspushurl", ".lfsconfig"):
            with self.subTest(key=key):
                config = self.source / ".lfsconfig"
                if key == ".lfsconfig":
                    self.git(self.source, "config", "--file", str(config), "lfs.url", endpoint)
                else:
                    self.git(self.source, "config", key, endpoint)
                configuration = (self.source / ".git/config").read_bytes()

                result = self.git(
                    self.source, "push", "drive", "main", expect_success=False,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("lfs.url" if key == ".lfsconfig" else key, result.stderr)
                self.assertEqual((self.source / ".git/config").read_bytes(), configuration)
                self.assertEqual((self.source / ".git/hooks/pre-push").read_bytes(), hook)
                self.assertEqual(self.git(self.source, "ls-remote", "drive").stdout, "")
                if key == ".lfsconfig":
                    self.assertIn(endpoint, config.read_text())
                    config.unlink()
                else:
                    self.git(self.source, "config", "--unset", key)

    def test_failed_upload_does_not_advance_git_refs(self):
        self.commit_binary(b"published\x00" * 1024, "published binary")
        self.git(self.source, "push", "drive", "main")
        before = self.git(self.source, "ls-remote", "drive", "refs/heads/main").stdout

        objects = self.drive / "repo" / ".git-remote-gdrive" / "lfs" / "objects"
        objects.rename(objects.with_name("saved-objects"))
        objects.write_text("uploads blocked", encoding="utf-8")
        self.commit_binary(b"unpublished\xff" * 1024, "unpublished binary")
        returncodes = []
        for executable in ("git", "git-lfs-gdrive"):
            with self.subTest(command=executable):
                process = self.command(
                    self.source, executable, "push", "drive", "main",
                    expect_success=False,
                )
                returncodes.append(process.returncode)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn("failed to push some refs", process.stderr)
                self.assertEqual(
                    self.git(
                        self.source, "ls-remote", "drive", "refs/heads/main"
                    ).stdout,
                    before,
                )
        self.assertEqual(returncodes[0], returncodes[1])

    def test_install_keeps_other_lfs_remote_independent(self):
        other = self.base / "other.git"
        self.git(
            self.base, "init", "--bare", "--quiet", "--initial-branch", "main", str(other)
        )
        self.git(self.source, "remote", "add", "other", other.as_uri())
        self.command(self.source, "git-lfs-gdrive", "install", "drive")
        binary = b"other remote\x00" * 1024
        self.commit_binary(binary, "binary for other remote")
        process = self.git(self.source, "push", "other", "main")
        self.assertNotIn("LFS upload ", process.stdout + process.stderr)
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
