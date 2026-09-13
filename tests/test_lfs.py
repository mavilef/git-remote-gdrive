import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestLFSCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        self.environment.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GDRIVE_LOCAL_ROOT": str(self.base / "drive"),
            "GDRIVE_CACHE_DIR": str(self.base / "cache"),
        })
        if not shutil.which("git-lfs", path=self.environment["PATH"]):
            self.skipTest("Git LFS is not installed")
        self.repo = self.new_repo("repo")

    def run_command(self, *arguments, directory=None, success=True, input_text=None):
        result = subprocess.run(
            arguments, cwd=directory or self.repo, env=self.environment,
            input=input_text, capture_output=True, text=True, check=False,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def git(self, *arguments, **kwargs):
        return self.run_command("git", *arguments, **kwargs)

    def cli(self, *arguments, **kwargs):
        return self.run_command(sys.executable, "-m", "git_remote_gdrive.lfs", *arguments, **kwargs)

    def new_repo(self, name):
        repo = self.base / name
        repo.mkdir()
        self.git("init", "--quiet", "--initial-branch=main", directory=repo)
        self.git("config", "user.name", "LFS Test", directory=repo)
        self.git("config", "user.email", "lfs@example.com", directory=repo)
        self.git("remote", "add", "drive", "gd://read-folder", directory=repo)
        return repo

    def test_install_is_idempotent_and_only_selects_drive_endpoints(self):
        self.git("remote", "add", "origin", "https://example.com/repo.git")
        self.git("config", "remote.origin.lfsurl", "https://lfs.example.com/repo")
        self.cli("install", "drive")
        configuration = (self.repo / ".git/config").read_bytes()
        hook = (self.repo / ".git/hooks/pre-push").read_bytes()

        self.cli("install", "drive")

        self.assertEqual((self.repo / ".git/config").read_bytes(), configuration)
        self.assertEqual((self.repo / ".git/hooks/pre-push").read_bytes(), hook)
        self.assertIn(b"git lfs pre-push", hook)
        endpoint = self.git("config", "--get", "remote.drive.lfsurl").stdout.strip()
        self.assertEqual(
            self.git("config", "--get-urlmatch", "lfs.standalonetransferagent", endpoint).stdout.strip(),
            "gdrive",
        )
        self.assertNotEqual(self.git("config", "--get", "lfs.standalonetransferagent", success=False).returncode, 0)
        self.assertNotEqual(self.git(
            "config", "--get-urlmatch", "lfs.standalonetransferagent",
            "https://lfs.example.com/repo", success=False,
        ).returncode, 0)
        self.assertEqual(
            self.git("config", "--get", "remote.origin.lfsurl").stdout.strip(),
            "https://lfs.example.com/repo",
        )

    def test_separate_fetch_and_push_urls_route_objects_to_the_correct_folder(self):
        self.git("remote", "set-url", "--push", "drive", "gdrive::write-folder")
        self.cli("install", "drive")
        self.assertTrue(self.git("config", "--get", "remote.drive.lfsurl").stdout.strip().endswith("/read-folder"))
        self.assertTrue(self.git("config", "--get", "remote.drive.lfspushurl").stdout.strip().endswith("/write-folder"))
        read_folder = self.base / "drive/read-folder"
        write_folder = self.base / "drive/write-folder"
        read_folder.mkdir(parents=True)
        write_folder.mkdir()

        def transfer(remote, operation, content):
            oid = hashlib.sha256(content).hexdigest()
            source = self.base / oid
            source.write_bytes(content)
            messages = [
                {"event": "init", "remote": remote, "operation": operation},
                {"event": operation, "oid": oid, "size": len(content), "path": str(source)},
                {"event": "terminate"},
            ]
            result = self.cli(input_text="".join(json.dumps(message) + "\n" for message in messages))
            completion = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(completion["event"], "complete")
            self.assertNotIn("error", completion)
            return oid

        fetch_oid = transfer("gd://read-folder", "upload", b"fetch content")
        push_oid = transfer("drive", "upload", b"push content")
        self.assertEqual(next(write_folder.rglob(push_oid)).read_bytes(), b"push content")
        self.assertFalse(list(read_folder.rglob(push_oid)))
        self.assertFalse(list(write_folder.rglob(fetch_oid)))
        transfer("drive", "download", b"fetch content")

    def test_invalid_or_ambiguous_remotes_are_rejected_before_installation(self):
        self.git("remote", "add", "origin", "https://example.com/repo.git")
        self.git("remote", "set-url", "--push", "drive", "gd://first")
        self.git("remote", "set-url", "--add", "--push", "drive", "gd://second")
        configuration = (self.repo / ".git/config").read_bytes()
        for remote in ("origin", "missing", "drive"):
            with self.subTest(remote=remote):
                result = self.cli("install", remote, success=False)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual((self.repo / ".git/config").read_bytes(), configuration)
                self.assertFalse((self.repo / ".git/hooks/pre-push").exists())

    def test_repository_wide_lfs_endpoints_are_rejected_without_config_changes(self):
        for key in ("lfs.url", "lfs.pushurl"):
            with self.subTest(key=key):
                self.git("config", key, "https://lfs.example.com/repo")
                configuration = (self.repo / ".git/config").read_bytes()
                result = self.cli("install", "drive", success=False)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"{key} overrides", result.stderr)
                self.assertEqual((self.repo / ".git/config").read_bytes(), configuration)
                self.git("config", "--unset", key)

    def test_lfsconfig_endpoint_overrides_are_found_in_worktree_index_and_head(self):
        for key in ("lfs.url", "lfs.pushurl"):
            for location in ("worktree", "index", "HEAD"):
                with self.subTest(key=key, location=location):
                    repo = self.new_repo(f"{key}-{location}")
                    config = repo / ".lfsconfig"
                    self.git("config", "--file", str(config), key, "https://lfs.example.com/repo", directory=repo)
                    if location != "worktree":
                        self.git("add", ".lfsconfig", directory=repo)
                        if location == "HEAD":
                            self.git("commit", "--quiet", "-m", "LFS endpoint", directory=repo)
                            self.git("rm", "--cached", ".lfsconfig", directory=repo)
                        config.unlink()
                    configuration = (repo / ".git/config").read_bytes()

                    result = self.cli("install", "drive", directory=repo, success=False)

                    self.assertEqual(result.returncode, 1)
                    self.assertIn(f"{key} overrides", result.stderr)
                    self.assertEqual((repo / ".git/config").read_bytes(), configuration)
                    self.assertFalse((repo / ".git/hooks/pre-push").exists())

    def test_install_preserves_an_existing_custom_pre_push_hook(self):
        hook = self.repo / ".git/hooks/pre-push"
        content = b"#!/bin/sh\nprintf 'custom hook\\n'\n"
        hook.write_bytes(content)
        hook.chmod(0o755)

        result = self.cli("install", "drive", success=False)

        self.assertEqual(result.returncode, 1)
        self.assertIn("Hook already exists", result.stderr)
        self.assertEqual(hook.read_bytes(), content)
        self.assertNotEqual(self.git("config", "--get", "remote.drive.lfsurl", success=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
