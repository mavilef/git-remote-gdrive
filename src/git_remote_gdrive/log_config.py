from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    level_name = os.environ.get("GDRIVE_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    logging.basicConfig(
        level=level,
        format="[%(asctime)s][git-remote-gdrive][%(funcName)s][%(levelname)s] %(message)s",
    )
