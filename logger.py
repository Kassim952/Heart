"""
=============================================================
  LOGGING MODULE
  Dual-output: rotating file + coloured console
=============================================================
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from config import (
    LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    LOG_LEVEL_CONSOLE, LOG_LEVEL_FILE, BOT_NAME
)

# ─── ANSI COLOUR CODES ───────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"
GREY   = "\033[90m"
MAGENTA= "\033[95m"

LEVEL_COLOURS = {
    "DEBUG":    GREY,
    "INFO":     CYAN,
    "WARNING":  YELLOW,
    "ERROR":    RED,
    "CRITICAL": f"{BOLD}{RED}",
}


# ─── FORMATTERS ──────────────────────────────────────────────────────────────

class ColourFormatter(logging.Formatter):
    """Console formatter with ANSI colours per log level."""

    def format(self, record: logging.LogRecord) -> str:
        colour = LEVEL_COLOURS.get(record.levelname, WHITE)
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = f"{colour}{record.levelname:<8}{RESET}"
        name  = f"{GREY}{record.name}{RESET}"
        msg   = super().format(record)
        # Strip the default formatted string and rebuild
        record.msg = record.getMessage()
        record.args = None
        plain_msg = record.msg
        return f"{GREY}{ts}{RESET} {level} {name}: {plain_msg}"


class FileFormatter(logging.Formatter):
    """Plain formatter for file output."""
    FORMAT = "%(asctime)s [%(levelname)-8s] [%(name)s] %(message)s"
    DATEFMT = "%Y-%m-%d %H:%M:%S"

    def __init__(self):
        super().__init__(fmt=self.FORMAT, datefmt=self.DATEFMT)


# ─── SETUP ───────────────────────────────────────────────────────────────────

def setup_logger(name: str = BOT_NAME) -> logging.Logger:
    """
    Create (or retrieve) a named logger with:
      - rotating file handler  → LOG_FILE
      - coloured console handler → stdout
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # Already configured

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # ── Ensure log directory exists ──
    log_dir = os.path.dirname(LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # ── Rotating file handler ──
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setLevel(getattr(logging, LOG_LEVEL_FILE, logging.DEBUG))
    fh.setFormatter(FileFormatter())
    logger.addHandler(fh)

    # ── Console handler ──
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(getattr(logging, LOG_LEVEL_CONSOLE, logging.INFO))
    ch.setFormatter(ColourFormatter())
    logger.addHandler(ch)

    return logger


def get_logger(sub_name: str) -> logging.Logger:
    """Return a child logger namespaced under the root bot logger."""
    root = setup_logger()
    return root.getChild(sub_name)


# ─── MODULE-LEVEL SINGLETON ──────────────────────────────────────────────────

log = setup_logger()
