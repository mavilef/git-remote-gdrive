import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from git_remote_gdrive.errors import ConfigurationError
from git_remote_gdrive.lfs_hooks import MANAGED_PRE_PUSH_HOOK, install_lfs_hooks


@unittest.skipUnless(shutil.which("git-lfs"), "Git LFS is not installed")
class TestLFSHooks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        self.environment.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
        self.git("init", "--quiet", "--initial-branch=main")
        self.hook = self.repo / ".git/hooks/pre-push"

    def git(self, *arguments, check=True):
        result = subprocess.run(
            ["git", *arguments], cwd=self.repo, env=self.environment,
            capture_output=True, text=True, check=False,
        )
        if check and result.returncode:
            raise ConfigurationError(result.stderr.strip() or result.stdout.strip())
        return result

    def test_install_is_idempotent_and_preserves_other_lfs_hooks(self):
        install_lfs_hooks(self.git)
        configuration = (self.repo / ".git/config").read_bytes()
        others = {
            name: (self.hook.parent / name).read_bytes()
            for name in ("post-checkout", "post-commit", "post-merge")
        }

        install_lfs_hooks(self.git)

        self.assertEqual(self.hook.read_bytes(), MANAGED_PRE_PUSH_HOOK)
        self.assertTrue(os.access(self.hook, os.X_OK))
        self.assertEqual((self.repo / ".git/config").read_bytes(), configuration)
        for name, content in others.items():
            self.assertEqual((self.hook.parent / name).read_bytes(), content)

    def test_upgrades_standard_hook_and_preserves_custom_hooks_on_reinstallation(self):
        self.git("lfs", "install", "--local")
        self.assertNotEqual(self.hook.read_bytes(), MANAGED_PRE_PUSH_HOOK)
        install_lfs_hooks(self.git)
        custom = self.hook.parent / "post-commit"
        content = b"#!/bin/sh\nprintf 'custom post-commit\\n'\n"
        custom.write_bytes(content)

        install_lfs_hooks(self.git)

        self.assertEqual(self.hook.read_bytes(), MANAGED_PRE_PUSH_HOOK)
        self.assertEqual(custom.read_bytes(), content)

    def test_preserves_custom_pre_push_hook(self):
        content = b"#!/bin/sh\nprintf 'custom pre-push\\n'\n"
        self.hook.write_bytes(content)

        with self.assertRaisesRegex(ConfigurationError, "Hook already exists"):
            install_lfs_hooks(self.git)

        self.assertEqual(self.hook.read_bytes(), content)

    def test_preserves_custom_commands_beyond_lfs_hook_detection_limit(self):
        self.git("lfs", "install", "--local")
        content = self.hook.read_bytes().ljust(1024, b"\n") + b"echo custom\n"
        self.hook.write_bytes(content)
        configuration = (self.repo / ".git/config").read_bytes()

        with self.assertRaisesRegex(ConfigurationError, "Hook already exists"):
            install_lfs_hooks(self.git)

        self.assertEqual(self.hook.read_bytes(), content)
        self.assertEqual((self.repo / ".git/config").read_bytes(), configuration)

    def test_local_hooks_path_works_when_installing_from_subdirectory(self):
        root = self.repo
        self.git("config", "core.hooksPath", ".custom-hooks")
        self.repo = root / "nested"
        self.repo.mkdir()

        install_lfs_hooks(self.git)

        self.assertEqual(
            (root / ".custom-hooks/pre-push").read_bytes(), MANAGED_PRE_PUSH_HOOK
        )
        self.assertFalse(self.hook.exists())

    def test_external_global_hooks_path_is_unchanged(self):
        shared = self.base / "shared-hooks"
        shared.mkdir()
        hook = shared / "pre-push"
        content = b"#!/bin/sh\ngit lfs pre-push \"$@\"\n"
        hook.write_bytes(content)
        global_config = self.base / "global.gitconfig"
        self.environment["GIT_CONFIG_GLOBAL"] = str(global_config)
        self.git("config", "--global", "core.hooksPath", str(shared))
        configuration = (self.repo / ".git/config").read_bytes()

        with self.assertRaisesRegex(ConfigurationError, "outside this repository"):
            install_lfs_hooks(self.git)

        self.assertEqual(hook.read_bytes(), content)
        self.assertEqual((self.repo / ".git/config").read_bytes(), configuration)
        self.assertEqual(list(shared.iterdir()), [hook])

    def test_symbolic_link_hook_is_unchanged(self):
        target = self.repo / "custom-hook"
        content = b"#!/bin/sh\ngit lfs pre-push \"$@\"\n"
        target.write_bytes(content)
        self.hook.symlink_to(target)
        configuration = (self.repo / ".git/config").read_bytes()

        with self.assertRaisesRegex(ConfigurationError, "symbolic-link"):
            install_lfs_hooks(self.git)

        self.assertTrue(self.hook.is_symlink())
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual((self.repo / ".git/config").read_bytes(), configuration)

    def test_linked_worktree_uses_common_hooks_directory(self):
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "--allow-empty", "--quiet", "-m", "initial")
        worktree = self.base / "worktree"
        self.git("worktree", "add", "--quiet", "-b", "other", str(worktree))
        self.repo = worktree

        install_lfs_hooks(self.git)
        install_lfs_hooks(self.git)

        self.assertEqual(self.hook.read_bytes(), MANAGED_PRE_PUSH_HOOK)

    def test_installs_and_reinstalls_hooks_in_bare_repository(self):
        self.repo = self.base / "bare.git"
        self.repo.mkdir()
        self.git("init", "--bare", "--quiet")

        install_lfs_hooks(self.git)
        install_lfs_hooks(self.git)

        hook = self.repo / "hooks/pre-push"
        self.assertEqual(hook.read_bytes(), MANAGED_PRE_PUSH_HOOK)
        self.assertTrue(os.access(hook, os.X_OK))

    @unittest.skipUnless(os.name == "posix", "shell hook requires POSIX")
    def test_hook_preserves_arguments_stdin_and_status_for_each_remote_kind(self):
        binary = self.base / "bin"
        binary.mkdir()
        output = self.base / "invocation.json"
        for name in ("git", "git-lfs-gdrive"):
            executable = binary / name
            executable.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "from pathlib import Path\n"
                f"Path({str(output)!r}).write_text(json.dumps({{"
                "'args': sys.argv, 'stdin': sys.stdin.read()}))\n"
                "sys.exit(7)\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
        self.hook.write_bytes(MANAGED_PRE_PUSH_HOOK)
        environment = dict(self.environment, PATH=str(binary))
        refs = "refs/heads/main " + "a" * 40 + " refs/heads/main " + "0" * 40 + "\n"
        urls = [
            "gd://folder", "gd::folder", "gdrive://folder", "gdrive::folder",
            "googledrive://folder", "googledrive::folder", "https://example.com/repo",
        ]
        for url in urls:
            with self.subTest(url=url):
                result = subprocess.run(
                    ["/bin/sh", str(self.hook), "drive remote", url],
                    env=environment, input=refs, capture_output=True, text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 7, result.stderr)
                invocation = json.loads(output.read_text())
                expected = ["pre-push", "drive remote", url]
                if url.startswith("https:"):
                    expected.insert(0, "lfs")
                    executable = "git"
                else:
                    executable = "git-lfs-gdrive"
                self.assertEqual(Path(invocation["args"][0]).name, executable)
                self.assertEqual(invocation["args"][1:], expected)
                self.assertEqual(invocation["stdin"], refs)


if __name__ == "__main__":
    unittest.main()
