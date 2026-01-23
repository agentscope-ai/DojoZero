"""OSS (Object Storage Service) utilities for uploading data to Alibaba Cloud OSS.

Credentials are handled by alibabacloud-credentials SDK:
    - Environment variables (ALIBABA_CLOUD_ACCESS_KEY_ID, etc.)
    - Credentials file (~/.alibabacloud/credentials)
    - ECS RAM role (automatic on ECS instances)
    - OIDC (K8s RRSA)

Environment variables for OSS config:
    DOJOZERO_OSS_BUCKET: OSS bucket name
    DOJOZERO_OSS_ENDPOINT: OSS endpoint (e.g., oss-cn-hangzhou.aliyuncs.com)
    DOJOZERO_OSS_PREFIX: Optional prefix for all OSS keys (e.g., "prod/")
"""

import logging
import os
from pathlib import Path

import oss2

logger = logging.getLogger(__name__)


class AlibabaCloudCredentialsProvider(oss2.credentials.CredentialsProvider):
    """oss2 CredentialsProvider that uses alibabacloud-credentials SDK.

    This provider automatically handles credential refresh for temporary
    credentials (ECS RAM role, OIDC, STS AssumeRole).
    """

    def __init__(self) -> None:
        from dojozero.core._credentials import get_credential_provider

        self._provider = get_credential_provider()

    def get_credentials(self) -> oss2.credentials.Credentials:
        """Get current credentials, refreshing if necessary."""
        creds = self._provider.get_credentials()

        if not creds.is_valid():
            raise ValueError(
                "No valid credentials found. Configure via: "
                "1) Environment variables (ALIBABA_CLOUD_ACCESS_KEY_ID), "
                "2) ~/.alibabacloud/credentials file, or "
                "3) ECS RAM role"
            )

        return oss2.credentials.Credentials(
            access_key_id=creds.access_key_id,
            access_key_secret=creds.access_key_secret,
            security_token=creds.security_token or "",
        )


class OSSClient:
    """Client for interacting with Alibaba Cloud OSS.

    Usage:
        # Using environment variables (recommended - handles credential refresh)
        client = OSSClient.from_env()
        client.upload_file("local/path/file.txt", "remote/path/file.txt")

        # With explicit configuration (static credentials)
        client = OSSClient(
            access_key_id="...",
            access_key_secret="...",
            bucket_name="my-bucket",
            endpoint="oss-cn-hangzhou.aliyuncs.com",
            prefix="prod/",
        )
    """

    def __init__(
        self,
        bucket_name: str,
        endpoint: str,
        prefix: str = "",
        *,
        credentials_provider: oss2.credentials.CredentialsProvider | None = None,
        access_key_id: str | None = None,
        access_key_secret: str | None = None,
        security_token: str | None = None,
    ):
        """Initialize OSS client.

        Args:
            bucket_name: OSS bucket name
            endpoint: OSS endpoint (e.g., oss-cn-hangzhou.aliyuncs.com)
            prefix: Optional prefix for all OSS keys (e.g., "prod/")
            credentials_provider: oss2 CredentialsProvider for automatic refresh
            access_key_id: OSS access key ID (for static credentials)
            access_key_secret: OSS access key secret (for static credentials)
            security_token: Optional STS security token (for static STS credentials)

        Either credentials_provider OR (access_key_id + access_key_secret) must be provided.
        """
        self.bucket_name = bucket_name
        self.endpoint = endpoint
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""

        # Initialize OSS auth
        if credentials_provider is not None:
            # Use ProviderAuthV4 for automatic credential refresh
            self._auth = oss2.ProviderAuthV4(credentials_provider)
        elif access_key_id and access_key_secret:
            # Static credentials
            if security_token:
                self._auth = oss2.StsAuth(
                    access_key_id, access_key_secret, security_token
                )
            else:
                self._auth = oss2.Auth(access_key_id, access_key_secret)
        else:
            raise ValueError(
                "Either credentials_provider or (access_key_id + access_key_secret) must be provided"
            )

        self._bucket = oss2.Bucket(self._auth, endpoint, bucket_name)

    @classmethod
    def from_env(
        cls,
        bucket_name: str | None = None,
        prefix: str | None = None,
    ) -> "OSSClient":
        """Create OSS client from environment/credentials.

        Credentials are resolved by alibabacloud-credentials SDK and automatically
        refreshed when they expire (for ECS RAM role, OIDC, STS AssumeRole).

        Credential resolution order:
        1. Environment variables (ALIBABA_CLOUD_ACCESS_KEY_ID, etc.)
        2. Credentials file (~/.alibabacloud/credentials)
        3. ECS RAM role (automatic on ECS instances)
        4. OIDC (K8s RRSA)

        Args:
            bucket_name: Override bucket name (default: from DOJOZERO_OSS_BUCKET)
            prefix: Override prefix (default: from DOJOZERO_OSS_PREFIX)

        Returns:
            OSSClient instance

        Raises:
            ValueError: If required configuration is not set
        """
        env_bucket = os.getenv("DOJOZERO_OSS_BUCKET")
        endpoint = os.getenv("DOJOZERO_OSS_ENDPOINT")
        env_prefix = os.getenv("DOJOZERO_OSS_PREFIX", "")

        if not endpoint:
            raise ValueError("DOJOZERO_OSS_ENDPOINT environment variable not set")

        final_bucket = bucket_name or env_bucket
        if not final_bucket:
            raise ValueError("Bucket name not provided and DOJOZERO_OSS_BUCKET not set")

        final_prefix = prefix if prefix is not None else env_prefix

        # Use AlibabaCloudCredentialsProvider for automatic credential refresh
        credentials_provider = AlibabaCloudCredentialsProvider()

        # Validate credentials are available (fail fast)
        credentials_provider.get_credentials()

        return cls(
            bucket_name=final_bucket,
            endpoint=endpoint,
            prefix=final_prefix,
            credentials_provider=credentials_provider,
        )

    def _make_key(self, key: str) -> str:
        """Apply prefix to key.

        Args:
            key: OSS object key

        Returns:
            Key with prefix applied
        """
        # Remove leading slash if present
        key = key.lstrip("/")
        return f"{self.prefix}{key}"

    def upload_file(self, local_path: str | Path, oss_key: str) -> str:
        """Upload a file to OSS.

        Args:
            local_path: Path to local file
            oss_key: OSS object key (prefix will be applied)

        Returns:
            Full OSS key (with prefix)

        Raises:
            FileNotFoundError: If local file does not exist
            oss2.exceptions.OssError: If upload fails
        """
        local_path = Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        full_key = self._make_key(oss_key)

        logger.info(
            "Uploading %s to oss://%s/%s", local_path, self.bucket_name, full_key
        )

        self._bucket.put_object_from_file(full_key, str(local_path))

        logger.info("Successfully uploaded %s", full_key)
        return full_key

    def upload_directory(
        self,
        local_dir: str | Path,
        oss_prefix: str,
        pattern: str = "*",
    ) -> list[str]:
        """Upload all files in a directory to OSS.

        Args:
            local_dir: Path to local directory
            oss_prefix: OSS prefix for uploaded files (under client prefix)
            pattern: Glob pattern to filter files (default: "*" for all files)

        Returns:
            List of uploaded OSS keys

        Raises:
            NotADirectoryError: If local_dir is not a directory
        """
        local_dir = Path(local_dir)
        if not local_dir.is_dir():
            raise NotADirectoryError(f"Not a directory: {local_dir}")

        uploaded_keys: list[str] = []

        for file_path in local_dir.glob(pattern):
            if file_path.is_file():
                # Preserve relative path structure
                relative_path = file_path.relative_to(local_dir)
                oss_key = f"{oss_prefix.rstrip('/')}/{relative_path}"
                full_key = self.upload_file(file_path, oss_key)
                uploaded_keys.append(full_key)

        return uploaded_keys

    def file_exists(self, oss_key: str) -> bool:
        """Check if a file exists in OSS.

        Args:
            oss_key: OSS object key (prefix will be applied)

        Returns:
            True if file exists, False otherwise
        """
        full_key = self._make_key(oss_key)
        return self._bucket.object_exists(full_key)

    def download_file(self, oss_key: str, local_path: str | Path) -> Path:
        """Download a file from OSS.

        Args:
            oss_key: OSS object key (prefix will be applied)
            local_path: Path to save the file locally

        Returns:
            Path to the downloaded file

        Raises:
            oss2.exceptions.NoSuchKey: If the file does not exist
            oss2.exceptions.OssError: If download fails
        """
        full_key = self._make_key(oss_key)
        local_path = Path(local_path)

        # Create parent directory if needed
        local_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Downloading oss://%s/%s to %s", self.bucket_name, full_key, local_path
        )

        self._bucket.get_object_to_file(full_key, str(local_path))

        logger.info("Successfully downloaded %s", local_path)
        return local_path

    def list_files(self, oss_prefix: str = "", pattern: str = "*") -> list[str]:
        """List files in OSS matching a prefix and pattern.

        Args:
            oss_prefix: OSS prefix to list (under client prefix)
            pattern: Glob pattern to filter files (default: "*" for all files).
                     Supports patterns like "*.jsonl", "2025-01-*/*.jsonl"

        Returns:
            List of OSS keys (without client prefix) matching the pattern
        """
        import fnmatch

        full_prefix = self._make_key(oss_prefix)
        matching_keys: list[str] = []

        # List all objects under the prefix
        for obj in oss2.ObjectIterator(self._bucket, prefix=full_prefix):
            # Get key relative to client prefix
            if self.prefix and obj.key.startswith(self.prefix):
                relative_key = obj.key[len(self.prefix) :]
            else:
                relative_key = obj.key

            # Apply glob pattern matching
            if fnmatch.fnmatch(relative_key, pattern):
                matching_keys.append(relative_key)

        return sorted(matching_keys)


def upload_file(
    local_path: str | Path,
    oss_key: str,
    bucket_name: str | None = None,
    prefix: str | None = None,
) -> str:
    """Convenience function to upload a single file to OSS.

    Uses environment variables for authentication.

    Args:
        local_path: Path to local file
        oss_key: OSS object key
        bucket_name: Override bucket name (default: from env)
        prefix: Override prefix (default: from env)

    Returns:
        Full OSS key (with prefix)
    """
    client = OSSClient.from_env(bucket_name=bucket_name, prefix=prefix)
    return client.upload_file(local_path, oss_key)


def upload_directory(
    local_dir: str | Path,
    oss_prefix: str,
    pattern: str = "*",
    bucket_name: str | None = None,
    prefix: str | None = None,
) -> list[str]:
    """Convenience function to upload a directory to OSS.

    Uses environment variables for authentication.

    Args:
        local_dir: Path to local directory
        oss_prefix: OSS prefix for uploaded files
        pattern: Glob pattern to filter files
        bucket_name: Override bucket name (default: from env)
        prefix: Override prefix (default: from env)

    Returns:
        List of uploaded OSS keys
    """
    client = OSSClient.from_env(bucket_name=bucket_name, prefix=prefix)
    return client.upload_directory(local_dir, oss_prefix, pattern)
