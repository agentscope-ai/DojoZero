"""Secure credential storage for DojoZero client.

Stores API keys in a file with restricted permissions (0600).
This keeps secrets out of CLI args, environment variables, and process listings.
"""

from __future__ import annotations

import json
import os
import stat

from dojozero_client._config import CONFIG_DIR

CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"


def save_api_key(api_key: str) -> None:
    """Save API key to credentials file with restricted permissions.

    Args:
        api_key: The API key to save

    The credentials file is created with mode 0600 (owner read/write only).
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Write credentials
    CREDENTIALS_FILE.write_text(json.dumps({"api_key": api_key}, indent=2))

    # Set restrictive permissions (owner read/write only)
    os.chmod(CREDENTIALS_FILE, stat.S_IRUSR | stat.S_IWUSR)


def load_api_key() -> str | None:
    """Load API key from credentials file.

    Returns:
        The API key if found, None otherwise
    """
    if not CREDENTIALS_FILE.exists():
        return None

    try:
        data = json.loads(CREDENTIALS_FILE.read_text())
        return data.get("api_key")
    except (json.JSONDecodeError, OSError):
        return None


def delete_api_key() -> bool:
    """Delete the stored API key.

    Returns:
        True if deleted, False if no credentials file existed
    """
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()
        return True
    return False


def has_api_key() -> bool:
    """Check if an API key is stored.

    Returns:
        True if credentials file exists and contains an API key
    """
    return load_api_key() is not None


__all__ = [
    "CREDENTIALS_FILE",
    "save_api_key",
    "load_api_key",
    "delete_api_key",
    "has_api_key",
]
