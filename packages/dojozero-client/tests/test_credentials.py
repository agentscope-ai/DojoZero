"""Tests for credentials module."""

import json
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch


from dojozero_client._credentials import (
    delete_api_key,
    has_api_key,
    load_api_key,
    save_api_key,
)


class TestSaveApiKey:
    """Tests for save_api_key function."""

    def test_saves_api_key_to_file(self):
        """Test API key is saved to credentials file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                with patch("dojozero_client._credentials.CONFIG_DIR", Path(tmpdir)):
                    save_api_key("sk-test-key-12345")

                    assert cred_file.exists()
                    data = json.loads(cred_file.read_text())
                    assert data["api_key"] == "sk-test-key-12345"

    def test_sets_restrictive_permissions(self):
        """Test credentials file has 0600 permissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                with patch("dojozero_client._credentials.CONFIG_DIR", Path(tmpdir)):
                    save_api_key("sk-test-key")

                    mode = os.stat(cred_file).st_mode
                    # Check only owner read/write (0600)
                    assert mode & stat.S_IRWXU == stat.S_IRUSR | stat.S_IWUSR
                    assert mode & stat.S_IRWXG == 0
                    assert mode & stat.S_IRWXO == 0

    def test_creates_parent_directory(self):
        """Test parent directory is created if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "subdir"
            cred_file = config_dir / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                with patch("dojozero_client._credentials.CONFIG_DIR", config_dir):
                    save_api_key("sk-test")

                    assert config_dir.exists()
                    assert cred_file.exists()

    def test_overwrites_existing_key(self):
        """Test saving new key overwrites existing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                with patch("dojozero_client._credentials.CONFIG_DIR", Path(tmpdir)):
                    save_api_key("old-key")
                    save_api_key("new-key")

                    data = json.loads(cred_file.read_text())
                    assert data["api_key"] == "new-key"


class TestLoadApiKey:
    """Tests for load_api_key function."""

    def test_returns_none_when_no_file(self):
        """Test returns None when credentials file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                result = load_api_key()
                assert result is None

    def test_loads_api_key_from_file(self):
        """Test loads API key from credentials file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(json.dumps({"api_key": "sk-loaded-key"}))

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                result = load_api_key()
                assert result == "sk-loaded-key"

    def test_returns_none_on_invalid_json(self):
        """Test returns None when file contains invalid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text("not valid json")

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                result = load_api_key()
                assert result is None

    def test_returns_none_when_key_missing(self):
        """Test returns None when api_key field is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(json.dumps({"other_field": "value"}))

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                result = load_api_key()
                assert result is None


class TestDeleteApiKey:
    """Tests for delete_api_key function."""

    def test_returns_false_when_no_file(self):
        """Test returns False when no credentials file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                result = delete_api_key()
                assert result is False

    def test_deletes_credentials_file(self):
        """Test deletes credentials file and returns True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(json.dumps({"api_key": "sk-test"}))

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                result = delete_api_key()

                assert result is True
                assert not cred_file.exists()


class TestHasApiKey:
    """Tests for has_api_key function."""

    def test_returns_false_when_no_file(self):
        """Test returns False when no credentials file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                assert has_api_key() is False

    def test_returns_true_when_key_exists(self):
        """Test returns True when API key is stored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(json.dumps({"api_key": "sk-test"}))

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                assert has_api_key() is True

    def test_returns_false_when_key_missing(self):
        """Test returns False when file exists but no api_key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(json.dumps({"other": "data"}))

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                assert has_api_key() is False
