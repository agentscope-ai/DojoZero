"""Tests for client configuration."""

import os
from pathlib import Path
from unittest.mock import patch


from dojozero_client._config import (
    ClientConfig,
    DEFAULT_DASHBOARD_URL,
    load_config,
)


class TestClientConfig:
    """Tests for ClientConfig dataclass."""

    def test_defaults(self):
        """Test default configuration values."""
        config = ClientConfig()
        assert config.dashboard_url == DEFAULT_DASHBOARD_URL
        assert config.dashboard_urls == []
        assert config.timeout == 30.0

    def test_is_sharded_false_by_default(self):
        """Test is_sharded returns False with no dashboard URLs."""
        config = ClientConfig()
        assert config.is_sharded is False

    def test_is_sharded_false_with_one_dashboard(self):
        """Test is_sharded returns False with single dashboard."""
        config = ClientConfig(dashboard_urls=["http://localhost:8000"])
        assert config.is_sharded is False

    def test_is_sharded_true_with_multiple_dashboards(self):
        """Test is_sharded returns True with multiple dashboards."""
        config = ClientConfig(
            dashboard_urls=["http://dashboard-a:8000", "http://dashboard-b:8000"]
        )
        assert config.is_sharded is True

    def test_get_discovery_urls_with_dashboard_url(self):
        """Test get_discovery_urls returns dashboard_url when no dashboard_urls."""
        config = ClientConfig(dashboard_url="http://localhost:8080")
        assert config.get_discovery_urls() == ["http://localhost:8080"]

    def test_get_discovery_urls_with_dashboards(self):
        """Test get_discovery_urls returns dashboard_urls when set."""
        config = ClientConfig(
            dashboard_url="http://localhost:8080",
            dashboard_urls=["http://dashboard-a:8000", "http://dashboard-b:8000"],
        )
        urls = config.get_discovery_urls()
        assert urls == ["http://dashboard-a:8000", "http://dashboard-b:8000"]

    def test_get_gateway_url(self):
        """Test get_gateway_url constructs correct URL."""
        config = ClientConfig(dashboard_url="http://localhost:8000")
        assert (
            config.get_gateway_url("trial-123")
            == "http://localhost:8000/api/trials/trial-123"
        )

    def test_get_gateway_url_strips_trailing_slash(self):
        """Test get_gateway_url strips trailing slash from dashboard_url."""
        config = ClientConfig(dashboard_url="http://localhost:8000/")
        assert (
            config.get_gateway_url("trial-123")
            == "http://localhost:8000/api/trials/trial-123"
        )


class TestLoadConfig:
    """Tests for load_config function."""

    def test_defaults(self):
        """Test loading with no overrides."""
        with patch.dict(os.environ, {}, clear=True):
            config = load_config(config_file=Path("/nonexistent/config.yaml"))
            assert config.dashboard_url == DEFAULT_DASHBOARD_URL
            assert config.dashboard_urls == []
            assert config.timeout == 30.0

    def test_explicit_dashboard_url(self):
        """Test explicit dashboard_url argument takes priority."""
        config = load_config(dashboard_url="http://explicit:8080")
        assert config.dashboard_url == "http://explicit:8080"

    def test_explicit_dashboard_urls(self):
        """Test explicit dashboard_urls argument takes priority."""
        config = load_config(
            dashboard_urls=["http://dashboard-a:8000", "http://dashboard-b:8000"]
        )
        assert config.dashboard_urls == [
            "http://dashboard-a:8000",
            "http://dashboard-b:8000",
        ]

    def test_explicit_timeout(self):
        """Test explicit timeout argument takes priority."""
        config = load_config(timeout=60.0)
        assert config.timeout == 60.0

    def test_env_var_dashboard_url(self):
        """Test DOJOZERO_DASHBOARD_URL environment variable."""
        with patch.dict(os.environ, {"DOJOZERO_DASHBOARD_URL": "http://env:8080"}):
            config = load_config()
            assert config.dashboard_url == "http://env:8080"

    def test_env_var_dashboard_urls(self):
        """Test DOJOZERO_DASHBOARD_URLS environment variable."""
        with patch.dict(
            os.environ,
            {"DOJOZERO_DASHBOARD_URLS": "http://dash-a:8000, http://dash-b:8000"},
        ):
            config = load_config()
            assert config.dashboard_urls == [
                "http://dash-a:8000",
                "http://dash-b:8000",
            ]

    def test_env_var_timeout(self):
        """Test DOJOZERO_TIMEOUT environment variable."""
        with patch.dict(os.environ, {"DOJOZERO_TIMEOUT": "45.5"}):
            config = load_config()
            assert config.timeout == 45.5

    def test_env_var_timeout_invalid(self):
        """Test invalid DOJOZERO_TIMEOUT is ignored."""
        with patch.dict(os.environ, {"DOJOZERO_TIMEOUT": "not-a-number"}):
            config = load_config()
            assert config.timeout == 30.0  # Default

    def test_explicit_overrides_env_var(self):
        """Test explicit argument overrides environment variable."""
        with patch.dict(os.environ, {"DOJOZERO_DASHBOARD_URL": "http://env:8080"}):
            config = load_config(dashboard_url="http://explicit:8080")
            assert config.dashboard_url == "http://explicit:8080"

    def test_config_file_not_found(self):
        """Test gracefully handles missing config file."""
        with patch.dict(os.environ, {}, clear=True):
            config = load_config(config_file=Path("/nonexistent/config.yaml"))
            assert config.dashboard_url == DEFAULT_DASHBOARD_URL
