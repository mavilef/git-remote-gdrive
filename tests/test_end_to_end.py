import hashlib
import os
import re
import shutil
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
    def test_clone_reports_bundle_bytes_and_honors_progress_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            drive = base / "drive"
            (drive / "repo").mkdir(parents=True)
            environment = {
                key: value for key, value in os.environ.items() if not key.startswith("GIT_")
            }
            environment.update({
                "PATH": os.pathsep.join((str(Path(sys.executable).parent), environment["PATH"])),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GDRIVE_LOCAL_ROOT": str(drive),
                "GDRIVE_CACHE_DIR": str(base / "cache"),
            })
            source = base / "source"
            source.mkdir()
            run(source, environment, "init", "--quiet", "--initial-branch", "main")
            run(source, environment, "config", "user.name", "Test User")
            run(source, environment, "config", "user.email", "test@example.com")
            content = os.urandom(9 * 1024 * 1024)
            checksum = hashlib.sha256(content).hexdigest()
            (source / "large.bin").write_bytes(content)
            run(source, environment, "add", "large.bin")
            run(source, environment, "commit", "--quiet", "-m", "large ordinary file")
            run(source, environment, "push", "gd://repo", "main")

            for index, (options, visible) in enumerate((
                (("--progress",), True),
                (("--quiet",), False),
                (("--no-progress",), False),
                (("--quiet", "--progress"), True),
            )):
                with self.subTest(options=options):
                    environment["GDRIVE_CACHE_DIR"] = str(base / f"cache-{index}")
                    clone = base / f"clone-{index}"
                    result = run(base, environment, "clone", *options, "gd://repo", str(clone))
                    self.assertEqual(hashlib.sha256((clone / "large.bin").read_bytes()).hexdigest(), checksum)
                    self.assertEqual(run(clone, environment, "status", "--porcelain").stdout, "")
                    run(clone, environment, "fsck", "--full")
                    self.assertNotIn("Drive download", result.stdout)
                    if visible:
                        percentages = [float(value) for value in re.findall(
                            r"Drive download .*?: ([0-9.]+)%", result.stderr,
                        )]
                        self.assertTrue(any(0 < value < 100 for value in percentages), result.stderr)
                        self.assertEqual(percentages[-1], 100.0)
                    else:
                        self.assertNotIn("Drive download", result.stderr)

            cached = run(base, environment, "clone", "--progress", "gd://repo", str(base / "cached-clone"))
            self.assertNotIn("Drive download", cached.stderr)

    @unittest.skipUnless(os.name == "posix", "fake Git executable requires POSIX")
    def test_plain_push_without_git_lfs_preserves_custom_hook_in_worktree_and_bare_repo(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            drive = base / "drive"
            binary = base / "bin"
            binary.mkdir()
            real_git = shutil.which("git")
            unavailable_log = base / "lfs-unavailable.log"
            fake_git = binary / "git"
            fake_git.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "from pathlib import Path\n"
                "if sys.argv[1:2] == ['lfs']:\n"
                f"    with Path({str(unavailable_log)!r}).open('a') as output:\n"
                "        output.write(' '.join(sys.argv[1:]) + '\\n')\n"
                "    sys.stderr.write(\"git: 'lfs' is not a git command\\n\")\n"
                "    sys.exit(1)\n"
                f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            environment = {
                key: value for key, value in os.environ.items() if not key.startswith("GIT_")
            }
            environment.update({
                "PATH": os.pathsep.join((
                    str(binary), str(Path(sys.executable).parent), environment["PATH"],
                )),
                # Git prepends its exec path for helpers; retain our wrapper there.
                "GIT_EXEC_PATH": str(binary),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GDRIVE_LOCAL_ROOT": str(drive),
                "GDRIVE_CACHE_DIR": str(base / "cache"),
            })
            source = base / "source"
            source.mkdir()
            run(source, environment, "init", "--quiet", "--initial-branch", "main")
            run(source, environment, "config", "user.name", "Test User")
            run(source, environment, "config", "user.email", "test@example.com")
            (source / "message.txt").write_text("ordinary content\n", encoding="utf-8")
            run(source, environment, "add", "message.txt")
            run(source, environment, "commit", "--quiet", "-m", "ordinary file")
            head = run(source, environment, "rev-parse", "HEAD").stdout.strip()

            for bare in (False, True):
                with self.subTest(bare=bare):
                    name = "bare" if bare else "plain"
                    (drive / name).mkdir(parents=True)
                    repo = base / "bare.git" if bare else source
                    if bare:
                        run(base, environment, "clone", "--quiet", "--bare", str(source), str(repo))
                    git_dir = repo if bare else repo / ".git"
                    hook = git_dir / "hooks/pre-push"
                    hook_content = b'#!/bin/sh\ncat > "$CUSTOM_HOOK_REFS"\n'
                    hook.write_bytes(hook_content)
                    hook.chmod(0o755)
                    refs = base / f"{name}-hook-refs"
                    environment["CUSTOM_HOOK_REFS"] = str(refs)
                    run(repo, environment, "remote", "add", "drive", f"gd://{name}")
                    configuration = (git_dir / "config").read_bytes()

                    run(repo, environment, "push", "drive", "main")

                    self.assertEqual(hook.read_bytes(), hook_content)
                    self.assertEqual((git_dir / "config").read_bytes(), configuration)
                    self.assertIn(f"refs/heads/main {head} ", refs.read_text())
                    remote_head = run(repo, environment, "ls-remote", "drive", "main")
                    self.assertEqual(remote_head.stdout.split()[0], head)
                    clone = base / f"{name}-clone"
                    run(base, environment, "clone", "--quiet", f"gd://{name}", str(clone))
                    self.assertEqual((clone / "message.txt").read_text(), "ordinary content\n")
            self.assertEqual(unavailable_log.read_text().splitlines(), ["lfs version"] * 4)

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
