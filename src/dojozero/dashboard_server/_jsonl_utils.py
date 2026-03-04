"""JSONL file utilities for trial monitoring.

Provides functions to extract information from JSONL event files without
fully parsing all events, for efficient monitoring of trial state.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_game_result_from_jsonl(jsonl_path: Path) -> dict | None:
    """Check if JSONL contains a game_result event.

    Scans the file for game_result event. This is used as a backup mechanism
    to detect game completion when the in-memory callback chain fails.

    Args:
        jsonl_path: Path to the JSONL file

    Returns:
        The game_result event dict, or None if not found
    """
    if not jsonl_path.exists():
        return None

    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("event_type") == "event.game_result":
                        return event
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning("Failed to read JSONL file %s: %s", jsonl_path, e)
        return None

    return None


def get_jsonl_last_modified(jsonl_path: Path) -> datetime | None:
    """Get last modified time of JSONL file for health monitoring.

    Uses file system mtime rather than parsing events, for efficiency.

    Args:
        jsonl_path: Path to the JSONL file

    Returns:
        Last modified datetime (UTC), or None if file doesn't exist
    """
    if not jsonl_path.exists():
        return None

    try:
        return datetime.fromtimestamp(jsonl_path.stat().st_mtime, tz=timezone.utc)
    except OSError as e:
        logger.warning("Failed to stat JSONL file %s: %s", jsonl_path, e)
        return None


__all__ = ["extract_game_result_from_jsonl", "get_jsonl_last_modified"]
