"""Configuration management for DojoZero client.

Supports layered configuration:
  CLI args > Environment vars > Config file > Defaults

Environment variables:
  DOJOZERO_DASHBOARD_URL: Dashboard server URL
  DOJOZERO_DASHBOARD_URLS: Comma-separated dashboard URLs (sharded mode)
  DOJOZERO_TIMEOUT: Connection timeout in seconds

Config file (~/.dojozero/config.yaml):
  # Dashboard server URL (required for remote access)
  dashboard_url: http://localhost:8000

  # Multiple dashboards for sharded mode (optional)
  # dashboard_urls:
  #   - http://dashboard-a:8000
  #   - http://dashboard-b:8000

  # Connection timeout in seconds (optional)
  # timeout: 30
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DASHBOARD_URL = "http://localhost:8000"
CONFIG_FILE_NAME = "config.yaml"
CONFIG_DIR = Path.home() / ".dojozero"

# Unified daemon paths
SOCKET_PATH = CONFIG_DIR / "daemon.sock"
PID_FILE = CONFIG_DIR / "daemon.pid"
TRIALS_DIR = CONFIG_DIR / "trials"

# Default config template with comments
DEFAULT_CONFIG_TEMPLATE = """\
# DojoZero Client Configuration
# See: https://github.com/anthropics/dojozero

# Dashboard server URL
# For local development: http://localhost:8000
# For remote servers: http://your-server:8000
dashboard_url: http://localhost:8000

# Connection timeout in seconds (optional)
# timeout: 30

# Multiple dashboards for sharded mode (optional)
# dashboard_urls:
#   - http://dashboard-a:8000
#   - http://dashboard-b:8000
"""


def _default_dashboard_urls() -> list[str]:
    return []


@dataclass
class ClientConfig:
    """Configuration for DojoClient."""

    dashboard_url: str = DEFAULT_DASHBOARD_URL
    dashboard_urls: list[str] = field(default_factory=_default_dashboard_urls)
    timeout: float = 30.0

    @property
    def is_sharded(self) -> bool:
        """Check if using sharded dashboard mode."""
        return len(self.dashboard_urls) > 1

    def get_discovery_urls(self) -> list[str]:
        """Get URLs to query for trial discovery.

        Returns dashboard_urls if configured, otherwise [dashboard_url].
        """
        if self.dashboard_urls:
            return self.dashboard_urls
        return [self.dashboard_url]

    def get_gateway_url(self, trial_id: str) -> str:
        """Construct gateway URL for a trial.

        Args:
            trial_id: Trial identifier

        Returns:
            Gateway URL: {dashboard_url}/api/trials/{trial_id}
        """
        base = self.dashboard_url.rstrip("/")
        return f"{base}/api/trials/{trial_id}"


def has_config() -> bool:
    """Check if config file exists.

    Returns:
        True if config file exists
    """
    config_file = CONFIG_DIR / CONFIG_FILE_NAME
    return config_file.exists()


def save_config(
    dashboard_url: str,
    timeout: float | None = None,
) -> None:
    """Save configuration to config file.

    Args:
        dashboard_url: Dashboard server URL
        timeout: Connection timeout (optional)
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_file = CONFIG_DIR / CONFIG_FILE_NAME

    config_data: dict[str, Any] = {
        "dashboard_url": dashboard_url,
    }
    if timeout is not None:
        config_data["timeout"] = timeout

    # Write with comments
    content = f"""\
# DojoZero Client Configuration
# See: https://github.com/anthropics/dojozero

# Dashboard server URL
dashboard_url: {dashboard_url}
"""
    if timeout is not None:
        content += f"""
# Connection timeout in seconds
timeout: {timeout}
"""

    config_file.write_text(content)


def create_default_config() -> None:
    """Create default config file if it doesn't exist."""
    if has_config():
        return

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_file = CONFIG_DIR / CONFIG_FILE_NAME
    config_file.write_text(DEFAULT_CONFIG_TEMPLATE)


def load_config(
    dashboard_url: str | None = None,
    dashboard_urls: list[str] | None = None,
    timeout: float | None = None,
    config_file: Path | None = None,
) -> ClientConfig:
    """Load configuration with layered precedence.

    Priority (highest to lowest):
      1. Explicit arguments
      2. Environment variables
      3. Config file
      4. Defaults

    Args:
        dashboard_url: Override dashboard URL
        dashboard_urls: Override dashboard URLs (sharded mode)
        timeout: Override timeout
        config_file: Override config file path

    Returns:
        Resolved ClientConfig
    """
    # Start with defaults
    config = ClientConfig()

    # Layer 3: Config file
    file_config = _load_config_file(config_file)
    if file_config:
        if "dashboard_url" in file_config:
            config.dashboard_url = file_config["dashboard_url"]
        if "dashboard_urls" in file_config:
            config.dashboard_urls = file_config["dashboard_urls"]
        if "timeout" in file_config:
            config.timeout = file_config["timeout"]

    # Layer 2: Environment variables
    if env_dashboard := os.environ.get("DOJOZERO_DASHBOARD_URL"):
        config.dashboard_url = env_dashboard

    if env_dashboards := os.environ.get("DOJOZERO_DASHBOARD_URLS"):
        config.dashboard_urls = [u.strip() for u in env_dashboards.split(",")]

    if env_timeout := os.environ.get("DOJOZERO_TIMEOUT"):
        try:
            config.timeout = float(env_timeout)
        except ValueError:
            pass

    # Layer 1: Explicit arguments (highest priority)
    if dashboard_url is not None:
        config.dashboard_url = dashboard_url
    if dashboard_urls is not None:
        config.dashboard_urls = dashboard_urls
    if timeout is not None:
        config.timeout = timeout

    return config


def _load_config_file(config_file: Path | None = None) -> dict[str, Any] | None:
    """Load config from YAML file.

    Args:
        config_file: Explicit config file path, or None to use default

    Returns:
        Config dict or None if file doesn't exist
    """
    if config_file is None:
        config_file = CONFIG_DIR / CONFIG_FILE_NAME

    if not config_file.exists():
        return None

    try:
        return yaml.safe_load(config_file.read_text())
    except Exception:
        return None


__all__ = [
    "ClientConfig",
    "CONFIG_DIR",
    "DEFAULT_DASHBOARD_URL",
    "PID_FILE",
    "SOCKET_PATH",
    "TRIALS_DIR",
    "create_default_config",
    "has_config",
    "load_config",
    "save_config",
]
