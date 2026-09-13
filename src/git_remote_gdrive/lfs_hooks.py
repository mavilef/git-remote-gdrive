from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .errors import ConfigurationError


MANAGED_PRE_PUSH_HOOK = b'''#!/bin/sh
# Installed by git-lfs-gdrive install.
case "$2" in
    gd://*|gd::*|gdrive://*|gdrive::*|googledrive://*|googledrive::*)
        command -v git-lfs-gdrive >/dev/null 2>&1 || {
            printf >&2 '%s\\n' "git-lfs-gdrive is required to push LFS files to Google Drive; install it and retry."
            exit 2
        }
        exec git-lfs-gdrive pre-push "$@"
        ;;
    *) exec git lfs pre-push "$@" ;;
esac
'''


def install_lfs_hooks(
    git: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Install byte progress without replacing custom or shared external hooks."""
    # Resolve the directory first: asking Git for the full hook path follows a
    # pre-push symlink before we can check whether it is safe to replace.
    hook = Path(
        git("rev-parse", "--path-format=absolute", "--git-path", "hooks").stdout.strip()
    ) / "pre-push"
    roots = [
        Path(git("rev-parse", "--path-format=absolute", option).stdout.strip()).resolve()
        for option in ("--show-toplevel", "--git-common-dir")
    ]
    if hook.is_symlink():
        raise ConfigurationError(f"Refusing to replace a symbolic-link hook: {hook}")
    if not any(hook.resolve().is_relative_to(root) for root in roots):
        raise ConfigurationError(
            f"core.hooksPath points outside this repository: {hook.parent}; "
            "integrate git-lfs-gdrive pre-push into your existing hooks manually"
        )
    existing = hook.read_bytes() if hook.exists() else None
    if existing == MANAGED_PRE_PUSH_HOOK:
        # Git LFS treats our hook as custom; retain it on repeated installation.
        git("lfs", "install", "--local", "--skip-repo")
    else:
        # Git LFS reads only the first 1024 bytes when recognizing its hooks.
        # Reject longer hooks rather than risk discarding unseen custom commands.
        if existing is not None and len(existing) > 1024:
            raise ConfigurationError(f"Hook already exists: {hook}; preserve custom hooks")
        git("lfs", "install", "--local")

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=hook.parent, prefix=".pre-push-", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(MANAGED_PRE_PUSH_HOOK)
        temporary.chmod(0o755)
        os.replace(temporary, hook)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
