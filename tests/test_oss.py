"""Unit tests for OSS utilities."""

from unittest.mock import MagicMock, patch

import pytest

from dojozero.utils.oss import OSSClient, upload_directory, upload_file


@pytest.fixture
def mock_credentials():
    """Mock the credential provider for tests."""
    from dojozero.core._credentials import Credentials

    mock_creds = Credentials(
        access_key_id="test-key-id",
        access_key_secret="test-key-secret",
        security_token=None,
    )
    mock_provider = MagicMock()
    mock_provider.get_credentials.return_value = mock_creds

    with patch(
        "dojozero.core._credentials.get_credential_provider", return_value=mock_provider
    ):
        yield mock_creds


@pytest.fixture
def mock_empty_credentials():
    """Mock the credential provider with empty credentials."""
    from dojozero.core._credentials import Credentials

    mock_creds = Credentials(
        access_key_id="",
        access_key_secret="",
        security_token=None,
    )
    mock_provider = MagicMock()
    mock_provider.get_credentials.return_value = mock_creds

    with patch(
        "dojozero.core._credentials.get_credential_provider", return_value=mock_provider
    ):
        yield mock_creds


class TestOSSClientInit:
    """Tests for OSSClient initialization."""

    def test_init_sets_attributes(self):
        """Test that __init__ sets all attributes correctly."""
        with patch("dojozero.utils.oss.oss2"):
            client = OSSClient(
                bucket_name="test-bucket",
                endpoint="oss-cn-hangzhou.aliyuncs.com",
                prefix="prod/",
                access_key_id="test-key-id",
                access_key_secret="test-key-secret",
            )

        assert client.bucket_name == "test-bucket"
        assert client.endpoint == "oss-cn-hangzhou.aliyuncs.com"
        assert client.prefix == "prod/"

    def test_init_empty_prefix(self):
        """Test that empty prefix stays empty."""
        with patch("dojozero.utils.oss.oss2"):
            client = OSSClient(
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="",
                access_key_id="key",
                access_key_secret="secret",
            )

        assert client.prefix == ""

    def test_init_prefix_without_trailing_slash(self):
        """Test that prefix without trailing slash gets one added."""
        with patch("dojozero.utils.oss.oss2"):
            client = OSSClient(
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="prod",
                access_key_id="key",
                access_key_secret="secret",
            )

        assert client.prefix == "prod/"

    def test_init_requires_credentials(self):
        """Test that __init__ requires either provider or static credentials."""
        with pytest.raises(ValueError, match="Either credentials_provider or"):
            OSSClient(
                bucket_name="bucket",
                endpoint="endpoint",
            )


class TestOSSClientFromEnv:
    """Tests for OSSClient.from_env() factory method."""

    def test_from_env_missing_credentials_raises(
        self, monkeypatch, mock_empty_credentials
    ):
        """Test that missing credentials raises ValueError."""
        monkeypatch.setenv("DOJOZERO_OSS_ENDPOINT", "endpoint")
        monkeypatch.setenv("DOJOZERO_OSS_BUCKET", "bucket")

        with pytest.raises(ValueError, match="No valid credentials found"):
            OSSClient.from_env()

    def test_from_env_missing_endpoint_raises(self, monkeypatch, mock_credentials):
        """Test that missing endpoint raises ValueError."""
        monkeypatch.delenv("DOJOZERO_OSS_ENDPOINT", raising=False)
        monkeypatch.setenv("DOJOZERO_OSS_BUCKET", "bucket")

        with pytest.raises(ValueError, match="DOJOZERO_OSS_ENDPOINT"):
            OSSClient.from_env()

    def test_from_env_missing_bucket_raises(self, monkeypatch, mock_credentials):
        """Test that missing bucket raises ValueError."""
        monkeypatch.setenv("DOJOZERO_OSS_ENDPOINT", "endpoint")
        monkeypatch.delenv("DOJOZERO_OSS_BUCKET", raising=False)

        with pytest.raises(ValueError, match="Bucket name not provided"):
            OSSClient.from_env()

    def test_from_env_bucket_override(self, monkeypatch, mock_credentials):
        """Test that bucket_name parameter overrides env var."""
        monkeypatch.setenv("DOJOZERO_OSS_ENDPOINT", "endpoint")
        monkeypatch.setenv("DOJOZERO_OSS_BUCKET", "env-bucket")
        monkeypatch.setenv("DOJOZERO_OSS_PREFIX", "")

        client = OSSClient.from_env(bucket_name="override-bucket")

        assert client.bucket_name == "override-bucket"

    def test_from_env_prefix_override(self, monkeypatch, mock_credentials):
        """Test that prefix parameter overrides env var."""
        monkeypatch.setenv("DOJOZERO_OSS_ENDPOINT", "endpoint")
        monkeypatch.setenv("DOJOZERO_OSS_BUCKET", "bucket")
        monkeypatch.setenv("DOJOZERO_OSS_PREFIX", "env-prefix/")

        client = OSSClient.from_env(prefix="override-prefix/")

        assert client.prefix == "override-prefix/"

    def test_from_env_prefix_override_with_empty_string(
        self, monkeypatch, mock_credentials
    ):
        """Test that empty string prefix override clears env prefix."""
        monkeypatch.setenv("DOJOZERO_OSS_ENDPOINT", "endpoint")
        monkeypatch.setenv("DOJOZERO_OSS_BUCKET", "bucket")
        monkeypatch.setenv("DOJOZERO_OSS_PREFIX", "env-prefix/")

        client = OSSClient.from_env(prefix="")

        assert client.prefix == ""

    def test_from_env_uses_env_prefix_when_not_overridden(
        self, monkeypatch, mock_credentials
    ):
        """Test that env prefix is used when not overridden."""
        monkeypatch.setenv("DOJOZERO_OSS_ENDPOINT", "endpoint")
        monkeypatch.setenv("DOJOZERO_OSS_BUCKET", "bucket")
        monkeypatch.setenv("DOJOZERO_OSS_PREFIX", "env-prefix")

        client = OSSClient.from_env()

        assert client.prefix == "env-prefix/"

    def test_from_env_uses_credentials_from_provider(
        self, monkeypatch, mock_credentials
    ):
        """Test that credentials come from the credential provider."""
        monkeypatch.setenv("DOJOZERO_OSS_ENDPOINT", "endpoint")
        monkeypatch.setenv("DOJOZERO_OSS_BUCKET", "bucket")

        with patch("dojozero.utils.oss.oss2"):
            client = OSSClient.from_env()

        # Verify client was created with correct bucket/endpoint
        assert client.bucket_name == "bucket"
        assert client.endpoint == "endpoint"


class TestOSSClientMakeKey:
    """Tests for OSSClient._make_key() method."""

    def _create_client(self, prefix: str = "") -> OSSClient:
        """Create a client for testing _make_key."""
        with patch("dojozero.utils.oss.oss2"):
            return OSSClient(
                access_key_id="key",
                access_key_secret="secret",
                bucket_name="bucket",
                endpoint="endpoint",
                prefix=prefix,
            )

    def test_make_key_no_prefix(self):
        """Test key generation without prefix."""
        client = self._create_client(prefix="")

        assert client._make_key("path/to/file.txt") == "path/to/file.txt"

    def test_make_key_with_prefix(self):
        """Test key generation with prefix."""
        client = self._create_client(prefix="prod/")

        assert client._make_key("path/to/file.txt") == "prod/path/to/file.txt"

    def test_make_key_strips_leading_slash(self):
        """Test that leading slash is stripped from key."""
        client = self._create_client(prefix="prod/")

        assert client._make_key("/path/to/file.txt") == "prod/path/to/file.txt"

    def test_make_key_strips_multiple_leading_slashes(self):
        """Test that multiple leading slashes are stripped."""
        client = self._create_client(prefix="")

        assert client._make_key("///path/to/file.txt") == "path/to/file.txt"


class TestOSSClientUploadFile:
    """Tests for OSSClient.upload_file() method."""

    def test_upload_file_missing_raises(self, tmp_path):
        """Test that uploading missing file raises FileNotFoundError."""
        with patch("dojozero.utils.oss.oss2"):
            client = OSSClient(
                access_key_id="key",
                access_key_secret="secret",
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="",
            )

        missing_file = tmp_path / "does_not_exist.txt"

        with pytest.raises(FileNotFoundError, match="Local file not found"):
            client.upload_file(missing_file, "remote/path.txt")

    def test_upload_file_calls_put_object_from_file(self, tmp_path):
        """Test that upload_file calls oss2 put_object_from_file."""
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        with patch("dojozero.utils.oss.oss2") as mock_oss2:
            mock_bucket = MagicMock()
            mock_oss2.Bucket.return_value = mock_bucket

            client = OSSClient(
                access_key_id="key",
                access_key_secret="secret",
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="",
            )

            result = client.upload_file(test_file, "remote/test.txt")

        mock_bucket.put_object_from_file.assert_called_once_with(
            "remote/test.txt", str(test_file)
        )
        assert result == "remote/test.txt"

    def test_upload_file_applies_prefix(self, tmp_path):
        """Test that upload_file applies prefix to key."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        with patch("dojozero.utils.oss.oss2") as mock_oss2:
            mock_bucket = MagicMock()
            mock_oss2.Bucket.return_value = mock_bucket

            client = OSSClient(
                access_key_id="key",
                access_key_secret="secret",
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="prod/",
            )

            result = client.upload_file(test_file, "data/test.txt")

        mock_bucket.put_object_from_file.assert_called_once_with(
            "prod/data/test.txt", str(test_file)
        )
        assert result == "prod/data/test.txt"


class TestOSSClientUploadDirectory:
    """Tests for OSSClient.upload_directory() method."""

    def test_upload_directory_not_a_directory_raises(self, tmp_path):
        """Test that uploading a file as directory raises NotADirectoryError."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        with patch("dojozero.utils.oss.oss2"):
            client = OSSClient(
                access_key_id="key",
                access_key_secret="secret",
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="",
            )

        with pytest.raises(NotADirectoryError, match="Not a directory"):
            client.upload_directory(test_file, "remote/")

    def test_upload_directory_uploads_all_files(self, tmp_path):
        """Test that upload_directory uploads all files."""
        # Create test directory structure
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")

        with patch("dojozero.utils.oss.oss2") as mock_oss2:
            mock_bucket = MagicMock()
            mock_oss2.Bucket.return_value = mock_bucket

            client = OSSClient(
                access_key_id="key",
                access_key_secret="secret",
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="",
            )

            result = client.upload_directory(tmp_path, "remote")

        assert len(result) == 2
        assert mock_bucket.put_object_from_file.call_count == 2

    def test_upload_directory_with_pattern(self, tmp_path):
        """Test that upload_directory respects glob pattern."""
        # Create test files
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.log").write_text("content2")
        (tmp_path / "file3.txt").write_text("content3")

        with patch("dojozero.utils.oss.oss2") as mock_oss2:
            mock_bucket = MagicMock()
            mock_oss2.Bucket.return_value = mock_bucket

            client = OSSClient(
                access_key_id="key",
                access_key_secret="secret",
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="",
            )

            result = client.upload_directory(tmp_path, "remote", pattern="*.txt")

        assert len(result) == 2
        assert mock_bucket.put_object_from_file.call_count == 2

    def test_upload_directory_preserves_relative_paths(self, tmp_path):
        """Test that upload_directory preserves relative path structure."""
        # Create nested structure
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("content")

        with patch("dojozero.utils.oss.oss2") as mock_oss2:
            mock_bucket = MagicMock()
            mock_oss2.Bucket.return_value = mock_bucket

            client = OSSClient(
                access_key_id="key",
                access_key_secret="secret",
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="",
            )

            client.upload_directory(tmp_path, "remote", pattern="**/*.txt")

        # Check that nested path is preserved
        calls = mock_bucket.put_object_from_file.call_args_list
        assert any("subdir/nested.txt" in str(call) for call in calls)


class TestOSSClientFileExists:
    """Tests for OSSClient.file_exists() method."""

    def test_file_exists_calls_object_exists(self):
        """Test that file_exists calls oss2 object_exists."""
        with patch("dojozero.utils.oss.oss2") as mock_oss2:
            mock_bucket = MagicMock()
            mock_bucket.object_exists.return_value = True
            mock_oss2.Bucket.return_value = mock_bucket

            client = OSSClient(
                access_key_id="key",
                access_key_secret="secret",
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="",
            )

            result = client.file_exists("path/to/file.txt")

        mock_bucket.object_exists.assert_called_once_with("path/to/file.txt")
        assert result is True

    def test_file_exists_applies_prefix(self):
        """Test that file_exists applies prefix."""
        with patch("dojozero.utils.oss.oss2") as mock_oss2:
            mock_bucket = MagicMock()
            mock_bucket.object_exists.return_value = False
            mock_oss2.Bucket.return_value = mock_bucket

            client = OSSClient(
                access_key_id="key",
                access_key_secret="secret",
                bucket_name="bucket",
                endpoint="endpoint",
                prefix="prod/",
            )

            result = client.file_exists("path/to/file.txt")

        mock_bucket.object_exists.assert_called_once_with("prod/path/to/file.txt")
        assert result is False


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_upload_file_creates_client_and_uploads(
        self, tmp_path, monkeypatch, mock_credentials
    ):
        """Test that upload_file convenience function works."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        monkeypatch.setenv("DOJOZERO_OSS_ENDPOINT", "endpoint")
        monkeypatch.setenv("DOJOZERO_OSS_BUCKET", "bucket")
        monkeypatch.setenv("DOJOZERO_OSS_PREFIX", "")

        with patch("dojozero.utils.oss.oss2") as mock_oss2:
            mock_bucket = MagicMock()
            mock_oss2.Bucket.return_value = mock_bucket

            result = upload_file(test_file, "remote/test.txt")

        assert result == "remote/test.txt"
        mock_bucket.put_object_from_file.assert_called_once()

    def test_upload_directory_creates_client_and_uploads(
        self, tmp_path, monkeypatch, mock_credentials
    ):
        """Test that upload_directory convenience function works."""
        (tmp_path / "file.txt").write_text("content")

        monkeypatch.setenv("DOJOZERO_OSS_ENDPOINT", "endpoint")
        monkeypatch.setenv("DOJOZERO_OSS_BUCKET", "bucket")
        monkeypatch.setenv("DOJOZERO_OSS_PREFIX", "")

        with patch("dojozero.utils.oss.oss2") as mock_oss2:
            mock_bucket = MagicMock()
            mock_oss2.Bucket.return_value = mock_bucket

            result = upload_directory(tmp_path, "remote/")

        assert len(result) == 1
        mock_bucket.put_object_from_file.assert_called_once()
