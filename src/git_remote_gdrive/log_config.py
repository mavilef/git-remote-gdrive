from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    level_name = os.environ.get("GDRIVE_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    logging.basicConfig(
        level=level,
        format="git-remote-gdrive: %(levelname)s: %(message)s",
    )
