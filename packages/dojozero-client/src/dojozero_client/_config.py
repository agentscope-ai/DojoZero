"""Configuration management for DojoZero client.

Supports layered configuration:
  CLI args > Environment vars > Config file > Defaults

Environment variables:
  DOJOZERO_GATEWAY_URL: Single gateway URL (standalone mode)
  DOJOZERO_DASHBOARD_URLS: Comma-separated dashboard URLs (sharded mode)

Config file (~/.dojozero/config.yaml):
  gateway_url: http://localhost:8000
  dashboard_urls:
    - http://dashboard-a:8000
    - http://dashboard-b:8000
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_GATEWAY_URL = "http://localhost:8000"
CONFIG_FILE_NAME = "config.yaml"
CONFIG_DIR = Path.home() / ".dojozero"


@dataclass
class ClientConfig:
    """Configuration for DojoClient."""

    gateway_url: str = DEFAULT_GATEWAY_URL
    dashboard_urls: list[str] = field(default_factory=list)
    timeout: float = 30.0

    @property
    def is_sharded(self) -> bool:
        """Check if using sharded dashboard mode."""
        return len(self.dashboard_urls) > 1

    def get_discovery_urls(self) -> list[str]:
        """Get URLs to query for trial discovery.

        Returns dashboard_urls if configured, otherwise [gateway_url].
        """
        if self.dashboard_urls:
            return self.dashboard_urls
        return [self.gateway_url]


def load_config(
    gateway_url: str | None = None,
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
        gateway_url: Override gateway URL
        dashboard_urls: Override dashboard URLs
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
        if "gateway_url" in file_config:
            config.gateway_url = file_config["gateway_url"]
        if "dashboard_urls" in file_config:
            config.dashboard_urls = file_config["dashboard_urls"]
        if "timeout" in file_config:
            config.timeout = file_config["timeout"]

    # Layer 2: Environment variables
    if env_gateway := os.environ.get("DOJOZERO_GATEWAY_URL"):
        config.gateway_url = env_gateway

    if env_dashboards := os.environ.get("DOJOZERO_DASHBOARD_URLS"):
        config.dashboard_urls = [u.strip() for u in env_dashboards.split(",")]

    if env_timeout := os.environ.get("DOJOZERO_TIMEOUT"):
        try:
            config.timeout = float(env_timeout)
        except ValueError:
            pass

    # Layer 1: Explicit arguments (highest priority)
    if gateway_url is not None:
        config.gateway_url = gateway_url
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
        import yaml

        return yaml.safe_load(config_file.read_text())
    except ImportError:
        # YAML not available, try JSON
        import json

        json_file = config_file.with_suffix(".json")
        if json_file.exists():
            return json.loads(json_file.read_text())
        return None
    except Exception:
        return None


__all__ = [
    "ClientConfig",
    "DEFAULT_GATEWAY_URL",
    "load_config",
]
