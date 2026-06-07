"""Logging: stdout (INFO) + rotating file (DEBUG)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FMT = "%(asctime)sZ %(levelname)s %(name)s %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def configure(log_path: Path, verbose: bool = False) -> None:
    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter(_FMT, _DATEFMT)
    formatter.converter = __import__("time").gmtime

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    rfh = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5)
    rfh.setLevel(logging.DEBUG)
    rfh.setFormatter(formatter)
    root.addHandler(rfh)

    # httpx + httpcore emit one INFO line per request -- noise during sync.
    # Keep them on at DEBUG only when -v is passed.
    quiet_level = logging.DEBUG if verbose else logging.WARNING
    logging.getLogger("httpx").setLevel(quiet_level)
    logging.getLogger("httpcore").setLevel(quiet_level)
