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
from .lfs_protocol import LFSTransferProtocol
from .lfs_push import push_with_progress
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


def _check_endpoint_overrides() -> None:
    # Git LFS gives lfs.url/pushurl precedence over remote-specific endpoints.
    # It also reads .lfsconfig from the worktree, index, or HEAD, in that order.
    root = Path(_git("rev-parse", "--show-toplevel").stdout.strip())
    if (root / ".lfsconfig").exists():
        config_source = ["--file", str(root / ".lfsconfig")]
    elif _git("cat-file", "-e", ":.lfsconfig", check=False).returncode == 0:
        config_source = ["--blob", ":.lfsconfig"]
    else:
        config_source = ["--blob", "HEAD:.lfsconfig"]
    for key in ("lfs.url", "lfs.pushurl"):
        for source in ([], config_source):
            result = _git("config", *source, "--get", key, check=False)
            if result.returncode == 0 and result.stdout.strip():
                raise ConfigurationError(
                    f"{key} overrides per-remote LFS settings; move it to "
                    "remote.<name>.lfsurl/lfspushurl before installing Drive LFS"
                )


def install(remote: str) -> None:
    download_folder = _folder_id(remote, "download", allow_url=False)
    upload_folder = _folder_id(remote, "upload", allow_url=False)
    version = _git("lfs", "version").stdout
    match = re.search(r"git-lfs/(\d+)\.(\d+)\.(\d+)", version)
    if match is None or tuple(map(int, match.groups())) < (3, 7, 1):
        raise ConfigurationError("Git LFS 3.7.1 or newer is required")
    _check_endpoint_overrides()
    # Keep filters local and let Git LFS report conflicting custom hooks.
    _git("lfs", "install", "--local")
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
        help="run git push with LFS percentages measured in bytes; forwards all arguments",
    )
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "push":
        try:
            return push_with_progress(arguments[1:], stdout=stdout)
        except (OSError, ValueError) as exc:
            print(f"git-lfs-gdrive: {exc}", file=sys.stderr)
            return 1
    args = parser.parse_args(arguments)
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
