"""Secure credential storage for DojoZero client.

Stores API keys in a file with restricted permissions (0600).
This keeps secrets out of CLI args, environment variables, and process listings.

Supports named profiles for running multiple agents on the same host:
    dojozero-agent config --profile alice --api-key sk-agent-alice
    dojozero-agent --profile alice daemon
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from dojozero_client._config import CONFIG_DIR

CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
DEFAULT_PROFILE = "default"


def _load_credentials() -> dict:
    """Load raw credentials data from file."""
    if not CREDENTIALS_FILE.exists():
        return {}

    try:
        data = json.loads(CREDENTIALS_FILE.read_text())
        # Migrate old format: {"api_key": "..."} -> {"profiles": {"default": {...}}}
        if "api_key" in data and "profiles" not in data:
            return {
                "default": DEFAULT_PROFILE,
                "profiles": {DEFAULT_PROFILE: {"api_key": data["api_key"]}},
            }
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _save_credentials(data: dict) -> None:
    """Save credentials data to file with restricted permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(CREDENTIALS_FILE, stat.S_IRUSR | stat.S_IWUSR)


def save_api_key(api_key: str, profile: str | None = None) -> None:
    """Save API key to credentials file with restricted permissions.

    Args:
        api_key: The API key to save
        profile: Profile name (default: "default")

    The credentials file is created with mode 0600 (owner read/write only).
    """
    profile = profile or DEFAULT_PROFILE
    data = _load_credentials()

    if "profiles" not in data:
        data["profiles"] = {}
    data["profiles"][profile] = {"api_key": api_key}

    # Set default profile if not set
    if "default" not in data:
        data["default"] = profile

    _save_credentials(data)


def load_api_key(profile: str | None = None) -> str | None:
    """Load API key from credentials file.

    Args:
        profile: Profile name. If None, uses default profile.

    Returns:
        The API key if found, None otherwise
    """
    data = _load_credentials()

    if not data:
        return None

    # Determine which profile to use
    if profile is None:
        profile = data.get("default", DEFAULT_PROFILE)

    profiles = data.get("profiles", {})
    profile_data = profiles.get(profile, {})
    return profile_data.get("api_key")


def delete_api_key(profile: str | None = None) -> bool:
    """Delete the stored API key for a profile.

    Args:
        profile: Profile name. If None, uses default profile.

    Returns:
        True if deleted, False if profile didn't exist
    """
    data = _load_credentials()

    if not data:
        return False

    if profile is None:
        profile = data.get("default", DEFAULT_PROFILE)

    profiles = data.get("profiles", {})
    if profile in profiles:
        del profiles[profile]
        # Update default if we deleted the default profile
        if data.get("default") == profile:
            data["default"] = next(iter(profiles), DEFAULT_PROFILE)
        _save_credentials(data)
        return True
    return False


def has_api_key(profile: str | None = None) -> bool:
    """Check if an API key is stored for a profile.

    Args:
        profile: Profile name. If None, uses default profile.

    Returns:
        True if credentials file exists and contains an API key for the profile
    """
    return load_api_key(profile) is not None


def get_default_profile() -> str:
    """Get the default profile name.

    Returns:
        The default profile name
    """
    data = _load_credentials()
    return data.get("default", DEFAULT_PROFILE)


def set_default_profile(profile: str) -> bool:
    """Set the default profile.

    Args:
        profile: Profile name to set as default

    Returns:
        True if profile exists and was set as default, False otherwise
    """
    data = _load_credentials()
    profiles = data.get("profiles", {})

    if profile not in profiles:
        return False

    data["default"] = profile
    _save_credentials(data)
    return True


def list_profiles() -> list[str]:
    """List all configured profiles.

    Returns:
        List of profile names
    """
    data = _load_credentials()
    return list(data.get("profiles", {}).keys())


def get_profile_dir(profile: str | None = None) -> Path:
    """Get the state directory for a profile.

    Args:
        profile: Profile name. If None, uses default profile.

    Returns:
        Path to the profile's state directory
    """
    if profile is None:
        profile = get_default_profile()

    # Default profile uses root config dir for backward compatibility
    if profile == DEFAULT_PROFILE:
        return CONFIG_DIR

    return CONFIG_DIR / "profiles" / profile


__all__ = [
    "CREDENTIALS_FILE",
    "DEFAULT_PROFILE",
    "save_api_key",
    "load_api_key",
    "delete_api_key",
    "has_api_key",
    "get_default_profile",
    "set_default_profile",
    "list_profiles",
    "get_profile_dir",
]
