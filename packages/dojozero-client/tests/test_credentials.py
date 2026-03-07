"""Tests for credentials module."""

import json
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch


from dojozero_client._credentials import (
    DEFAULT_PROFILE,
    delete_api_key,
    get_default_profile,
    get_profile_dir,
    has_api_key,
    list_profiles,
    load_api_key,
    save_api_key,
    set_default_profile,
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
                    assert data["profiles"]["default"]["api_key"] == "sk-test-key-12345"

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
                    assert data["profiles"]["default"]["api_key"] == "new-key"

    def test_saves_to_named_profile(self):
        """Test saving API key to a named profile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                with patch("dojozero_client._credentials.CONFIG_DIR", Path(tmpdir)):
                    save_api_key("sk-alice", profile="alice")

                    data = json.loads(cred_file.read_text())
                    assert data["profiles"]["alice"]["api_key"] == "sk-alice"
                    # First profile becomes default
                    assert data["default"] == "alice"

    def test_multiple_profiles(self):
        """Test saving multiple profiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                with patch("dojozero_client._credentials.CONFIG_DIR", Path(tmpdir)):
                    save_api_key("sk-alice", profile="alice")
                    save_api_key("sk-bob", profile="bob")

                    data = json.loads(cred_file.read_text())
                    assert data["profiles"]["alice"]["api_key"] == "sk-alice"
                    assert data["profiles"]["bob"]["api_key"] == "sk-bob"
                    # First profile remains default
                    assert data["default"] == "alice"


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
        """Test loads API key from credentials file (old format, migrated)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            # Old format - should be auto-migrated
            cred_file.write_text(json.dumps({"api_key": "sk-loaded-key"}))

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                result = load_api_key()
                assert result == "sk-loaded-key"

    def test_loads_api_key_new_format(self):
        """Test loads API key from new profile format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(
                json.dumps(
                    {
                        "default": "default",
                        "profiles": {"default": {"api_key": "sk-new-format"}},
                    }
                )
            )

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                result = load_api_key()
                assert result == "sk-new-format"

    def test_loads_named_profile(self):
        """Test loads API key from named profile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(
                json.dumps(
                    {
                        "default": "default",
                        "profiles": {
                            "default": {"api_key": "sk-default"},
                            "alice": {"api_key": "sk-alice"},
                        },
                    }
                )
            )

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                assert load_api_key() == "sk-default"
                assert load_api_key(profile="alice") == "sk-alice"
                assert load_api_key(profile="nonexistent") is None

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

    def test_deletes_profile_from_file(self):
        """Test deletes profile from credentials file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                with patch("dojozero_client._credentials.CONFIG_DIR", Path(tmpdir)):
                    save_api_key("sk-alice", profile="alice")
                    save_api_key("sk-bob", profile="bob")

                    result = delete_api_key(profile="alice")

                    assert result is True
                    # File still exists with remaining profile
                    assert cred_file.exists()
                    data = json.loads(cred_file.read_text())
                    assert "alice" not in data["profiles"]
                    assert "bob" in data["profiles"]

    def test_deletes_default_profile(self):
        """Test deleting default profile updates default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                with patch("dojozero_client._credentials.CONFIG_DIR", Path(tmpdir)):
                    save_api_key("sk-default")
                    save_api_key("sk-alice", profile="alice")

                    # Delete default profile
                    result = delete_api_key()
                    assert result is True

                    # Default should now point to remaining profile
                    data = json.loads(cred_file.read_text())
                    assert "default" not in data["profiles"]
                    assert data["default"] == "alice"


class TestHasApiKey:
    """Tests for has_api_key function."""

    def test_returns_false_when_no_file(self):
        """Test returns False when no credentials file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                assert has_api_key() is False

    def test_returns_true_when_key_exists(self):
        """Test returns True when API key is stored (old format)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(json.dumps({"api_key": "sk-test"}))

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                assert has_api_key() is True

    def test_returns_true_for_named_profile(self):
        """Test returns True for named profile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(
                json.dumps(
                    {
                        "default": "default",
                        "profiles": {"alice": {"api_key": "sk-alice"}},
                    }
                )
            )

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                assert has_api_key(profile="alice") is True
                assert has_api_key(profile="bob") is False

    def test_returns_false_when_key_missing(self):
        """Test returns False when file exists but no api_key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(json.dumps({"other": "data"}))

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                assert has_api_key() is False


class TestListProfiles:
    """Tests for list_profiles function."""

    def test_returns_empty_when_no_file(self):
        """Test returns empty list when no credentials file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                assert list_profiles() == []

    def test_returns_profile_names(self):
        """Test returns list of profile names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            cred_file.write_text(
                json.dumps(
                    {
                        "default": "alice",
                        "profiles": {
                            "alice": {"api_key": "sk-alice"},
                            "bob": {"api_key": "sk-bob"},
                        },
                    }
                )
            )

            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                profiles = list_profiles()
                assert "alice" in profiles
                assert "bob" in profiles


class TestDefaultProfile:
    """Tests for get/set default profile functions."""

    def test_get_default_returns_default_when_no_file(self):
        """Test returns 'default' when no credentials file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                assert get_default_profile() == DEFAULT_PROFILE

    def test_set_default_profile(self):
        """Test setting default profile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                with patch("dojozero_client._credentials.CONFIG_DIR", Path(tmpdir)):
                    save_api_key("sk-alice", profile="alice")
                    save_api_key("sk-bob", profile="bob")

                    assert set_default_profile("bob") is True
                    assert get_default_profile() == "bob"

    def test_set_default_nonexistent_profile(self):
        """Test setting nonexistent profile returns False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_file = Path(tmpdir) / "credentials.json"
            with patch("dojozero_client._credentials.CREDENTIALS_FILE", cred_file):
                with patch("dojozero_client._credentials.CONFIG_DIR", Path(tmpdir)):
                    save_api_key("sk-alice", profile="alice")

                    assert set_default_profile("nonexistent") is False


class TestGetProfileDir:
    """Tests for get_profile_dir function."""

    def test_default_profile_uses_config_dir(self):
        """Test default profile uses root config dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            with patch("dojozero_client._credentials.CONFIG_DIR", config_dir):
                result = get_profile_dir()
                assert result == config_dir

    def test_named_profile_uses_subdirectory(self):
        """Test named profile uses profiles subdirectory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            with patch("dojozero_client._credentials.CONFIG_DIR", config_dir):
                result = get_profile_dir(profile="alice")
                assert result == config_dir / "profiles" / "alice"
