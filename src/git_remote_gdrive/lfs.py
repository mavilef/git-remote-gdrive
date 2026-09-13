from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence, TextIO

from .app import build_drive_client
from .errors import ConfigurationError
from .lfs_hooks import install_lfs_hooks
from .lfs_protocol import LFSTransferProtocol
from .lfs_push import pre_push_with_progress
from .lfs_store import LFSObjectStore
from .log_config import configure_logging
from .utils import get_folder_id_from_google_drive_url


def _git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *arguments], capture_output=True, text=True, check=False
    )
    if check and result.returncode:
        raise ConfigurationError(
            result.stderr.strip() or result.stdout.strip() or "Git command failed"
        )
    return result


def _folder_id(remote: str, operation: str, *, allow_url: bool = True) -> str:
    options = ["--push"] if operation == "upload" else []
    result = _git("remote", "get-url", *options, "--all", "--", remote, check=False)
    if result.returncode:
        if not allow_url:
            raise ConfigurationError(result.stderr.strip() or "remote not found")
        url = remote
    else:
        urls = result.stdout.splitlines()
        if len(urls) != 1:
            raise ConfigurationError("LFS requires exactly one URL per remote direction")
        url = urls[0]
    if not url.startswith(
        ("gd://", "gd::", "gdrive://", "gdrive::", "googledrive://", "googledrive::")
    ):
        raise ConfigurationError(f"'{remote}' is not a Google Drive helper remote")
    try:
        return get_folder_id_from_google_drive_url(url)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc


def _build_store(remote: str, operation: str) -> LFSObjectStore:
    folder_id = _folder_id(remote, operation)
    cache = os.environ.get("GDRIVE_CACHE_DIR")
    return LFSObjectStore(
        build_drive_client(),
        folder_id,
        cache_dir=Path(cache).expanduser() if cache else None,
    )


def _lfs_config_sources() -> list[list[str]]:
    # Git LFS reads .lfsconfig from the worktree, index, or HEAD, in that order.
    worktree = _git("rev-parse", "--show-toplevel", check=False)
    root = Path(worktree.stdout.strip()) if worktree.returncode == 0 else None
    if root is not None and (root / ".lfsconfig").exists():
        config_source = ["--file", str(root / ".lfsconfig")]
    elif _git("cat-file", "-e", ":.lfsconfig", check=False).returncode == 0:
        config_source = ["--blob", ":.lfsconfig"]
    else:
        config_source = ["--blob", "HEAD:.lfsconfig"]
    return [[], config_source]


def _check_endpoint_overrides(
    *, keys: Sequence[str] = ("lfs.url", "lfs.pushurl")
) -> None:
    # These settings take precedence over remote-specific endpoints.
    sources = _lfs_config_sources()
    for key in keys:
        for source in sources:
            result = _git("config", *source, "--get", key, check=False)
            if result.returncode == 0 and result.stdout.strip():
                raise ConfigurationError(
                    f"{key} overrides per-remote LFS settings; move it to "
                    "remote.<name>.lfsurl/lfspushurl before installing Drive LFS"
                )


def _check_lfs_version(version: str) -> None:
    match = re.search(r"git-lfs/(\d+)\.(\d+)\.(\d+)", version)
    if match is None or tuple(map(int, match.groups())) < (3, 7, 1):
        raise ConfigurationError("Git LFS 3.7.1 or newer is required")


def prepare_push(remote: str) -> None:
    """Prepare LFS before Git runs pre-push; leave ordinary repositories alone."""
    version = _git("lfs", "version", check=False)
    if version.returncode:
        return
    # Include other branches and historical pointers, not just the checkout.
    if not _git("lfs", "ls-files", "--all", "--name-only").stdout.strip():
        return
    _check_lfs_version(version.stdout)
    _check_endpoint_overrides()
    key = f"remote.{remote}.lfspushurl"
    endpoint = _git("config", "--get", key, check=False).stdout.strip()
    if endpoint and not endpoint.startswith("https://git-remote-gdrive.invalid/"):
        raise ConfigurationError(
            f"{key} overrides Drive LFS uploads; remove it to use automatic Drive LFS"
        )
    install_lfs_hooks(_git)


def prepare_fetch(remote: str, url: str, object_ids: Sequence[str]) -> None:
    """Configure LFS downloads before checkout, including the first clone."""
    version = _git("lfs", "version", check=False)
    if version.returncode:
        return
    commits = []
    for object_id in dict.fromkeys(object_ids):
        result = _git("rev-parse", "--verify", f"{object_id}^{{commit}}", check=False)
        if result.returncode == 0:
            commits.append(result.stdout.strip())
    commits = list(dict.fromkeys(commits))
    if not any(
        _git("lfs", "ls-files", "--name-only", commit).stdout.strip()
        for commit in commits
    ):
        return
    _check_lfs_version(version.stdout)
    # Check effective configuration, not other branches fetched alongside the
    # selected revision. Git LFS reads that revision's .lfsconfig at checkout.
    _check_endpoint_overrides(keys=("lfs.url",))
    key = f"remote.{remote}.lfsurl"
    for source in _lfs_config_sources():
        existing = _git("config", *source, "--get", key, check=False).stdout.strip()
        if existing and not existing.startswith("https://git-remote-gdrive.invalid/"):
            raise ConfigurationError(
                f"{key} overrides Drive LFS downloads; remove it to use automatic Drive LFS"
            )
    folder = get_folder_id_from_google_drive_url(url)
    endpoint = f"https://git-remote-gdrive.invalid/{folder}"
    # Fetch must preserve custom hooks and the endpoint used for uploads.
    _git("lfs", "install", "--local", "--skip-repo")
    settings = {
        "lfs.customtransfer.gdrive.path": "git-lfs-gdrive",
        "lfs.customtransfer.gdrive.concurrent": "false",
        "lfs.customtransfer.gdrive.direction": "both",
        key: endpoint,
        f"lfs.{endpoint}.standalonetransferagent": "gdrive",
    }
    for key, value in settings.items():
        _git("config", "--local", "--replace-all", key, value)


def install(remote: str) -> None:
    download_folder = _folder_id(remote, "download", allow_url=False)
    upload_folder = _folder_id(remote, "upload", allow_url=False)
    _check_lfs_version(_git("lfs", "version").stdout)
    _check_endpoint_overrides()
    install_lfs_hooks(_git)
    settings = {
        "lfs.customtransfer.gdrive.path": "git-lfs-gdrive",
        "lfs.customtransfer.gdrive.concurrent": "false",
        "lfs.customtransfer.gdrive.direction": "both",
    }
    for folder, key in ((download_folder, "lfsurl"), (upload_folder, "lfspushurl")):
        # HTTPS gives Git LFS a stable, URL-matchable endpoint. No HTTP requests
        # are made; .invalid also prevents accidental fallback to an LFS server.
        endpoint = f"https://git-remote-gdrive.invalid/{folder}"
        settings[f"remote.{remote}.{key}"] = endpoint
        settings[f"lfs.{endpoint}.standalonetransferagent"] = "gdrive"
    for key, value in settings.items():
        _git("config", "--local", "--replace-all", key, value)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        prog="git-lfs-gdrive", description="Transfer Git LFS objects using Google Drive"
    )
    commands = parser.add_subparsers(dest="command")
    installer = commands.add_parser("install", help="configure LFS for a Drive remote")
    installer.add_argument("remote", help="name of an existing Google Drive remote")
    commands.add_parser(
        "push", add_help=False,
        help="compatibility alias for git push; forwards all arguments",
    )
    hook = commands.add_parser("pre-push", help="Git hook installed automatically")
    hook.add_argument("remote")
    hook.add_argument("url")
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "push":
        try:
            return subprocess.call(["git", "push", *arguments[1:]], stdout=stdout)
        except (OSError, ValueError) as exc:
            print(f"git-lfs-gdrive: {exc}", file=sys.stderr)
            return 1
    args = parser.parse_args(arguments)
    if args.command == "pre-push":
        try:
            return pre_push_with_progress([args.remote, args.url], stdout=stdout)
        except (OSError, ValueError) as exc:
            print(f"git-lfs-gdrive: {exc}", file=sys.stderr)
            return 1
    if args.command == "install":
        try:
            install(args.remote)
        except (ConfigurationError, OSError) as exc:
            print(f"git-lfs-gdrive: {exc}", file=sys.stderr)
            return 1
        print(f"Git LFS configured for remote '{args.remote}'.", file=stdout or sys.stdout)
        return 0
    return LFSTransferProtocol(_build_store, stdin or sys.stdin, stdout or sys.stdout).run()


if __name__ == "__main__":
    raise SystemExit(main())
