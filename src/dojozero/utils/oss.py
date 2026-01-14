"""OSS (Object Storage Service) utilities for uploading data to Alibaba Cloud OSS.

Environment variables:
    DOJOZERO_OSS_ACCESS_KEY_ID: OSS access key ID
    DOJOZERO_OSS_ACCESS_KEY_SECRET: OSS access key secret
    DOJOZERO_OSS_BUCKET: OSS bucket name
    DOJOZERO_OSS_ENDPOINT: OSS endpoint (e.g., oss-cn-hangzhou.aliyuncs.com)
    DOJOZERO_OSS_PREFIX: Optional prefix for all OSS keys (e.g., "prod/")
"""

import logging
import os
from pathlib import Path

import oss2

logger = logging.getLogger(__name__)


class OSSClient:
    """Client for interacting with Alibaba Cloud OSS.

    Usage:
        # Using environment variables
        client = OSSClient.from_env()
        client.upload_file("local/path/file.txt", "remote/path/file.txt")

        # With explicit configuration
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
        access_key_id: str,
        access_key_secret: str,
        bucket_name: str,
        endpoint: str,
        prefix: str = "",
    ):
        """Initialize OSS client.

        Args:
            access_key_id: OSS access key ID
            access_key_secret: OSS access key secret
            bucket_name: OSS bucket name
            endpoint: OSS endpoint (e.g., oss-cn-hangzhou.aliyuncs.com)
            prefix: Optional prefix for all OSS keys (e.g., "prod/")
        """
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.bucket_name = bucket_name
        self.endpoint = endpoint
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""

        # Initialize OSS auth and bucket
        self._auth = oss2.Auth(access_key_id, access_key_secret)
        self._bucket = oss2.Bucket(self._auth, endpoint, bucket_name)

    @classmethod
    def from_env(
        cls,
        bucket_name: str | None = None,
        prefix: str | None = None,
    ) -> "OSSClient":
        """Create OSS client from environment variables.

        Args:
            bucket_name: Override bucket name (default: from DOJOZERO_OSS_BUCKET)
            prefix: Override prefix (default: from DOJOZERO_OSS_PREFIX)

        Returns:
            OSSClient instance

        Raises:
            ValueError: If required environment variables are not set
        """
        access_key_id = os.getenv("DOJOZERO_OSS_ACCESS_KEY_ID")
        access_key_secret = os.getenv("DOJOZERO_OSS_ACCESS_KEY_SECRET")
        env_bucket = os.getenv("DOJOZERO_OSS_BUCKET")
        endpoint = os.getenv("DOJOZERO_OSS_ENDPOINT")
        env_prefix = os.getenv("DOJOZERO_OSS_PREFIX", "")

        if not access_key_id:
            raise ValueError("DOJOZERO_OSS_ACCESS_KEY_ID environment variable not set")
        if not access_key_secret:
            raise ValueError(
                "DOJOZERO_OSS_ACCESS_KEY_SECRET environment variable not set"
            )
        if not endpoint:
            raise ValueError("DOJOZERO_OSS_ENDPOINT environment variable not set")

        final_bucket = bucket_name or env_bucket
        if not final_bucket:
            raise ValueError("Bucket name not provided and DOJOZERO_OSS_BUCKET not set")

        final_prefix = prefix if prefix is not None else env_prefix

        return cls(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            bucket_name=final_bucket,
            endpoint=endpoint,
            prefix=final_prefix,
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
