"""OSS (Object Storage Service) utilities for uploading data to Alibaba Cloud OSS.

Requires optional dependency group ``dojozero[alicloud]`` (``oss2``, ``alibabacloud-credentials``).

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

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_oss2_module: Any = None
_AlibabaCloudCredentialsProvider: type | None = None

_MISSING_OSS = (
    "OSS requires optional dependencies. Install with: pip install 'dojozero[alicloud]'"
)


def _require_oss2() -> Any:
    """Import oss2 lazily; raise ImportError with install hint if missing."""
    global _oss2_module
    if _oss2_module is None:
        try:
            import oss2 as m

            _oss2_module = m
        except ImportError as e:
            raise ImportError(_MISSING_OSS) from e
    return _oss2_module


def _credentials_provider_type() -> type:
    global _AlibabaCloudCredentialsProvider
    if _AlibabaCloudCredentialsProvider is not None:
        return _AlibabaCloudCredentialsProvider
    oss = _require_oss2()

    class AlibabaCloudCredentialsProvider(oss.credentials.CredentialsProvider):
        """oss2 CredentialsProvider that uses alibabacloud-credentials SDK."""

        def __init__(self) -> None:
            from dojozero.core._credentials import get_credential_provider

            self._provider = get_credential_provider()

        def get_credentials(self) -> Any:
            creds = self._provider.get_credentials()

            if not creds.is_valid():
                raise ValueError(
                    "No valid credentials found. Configure via: "
                    "1) Environment variables (ALIBABA_CLOUD_ACCESS_KEY_ID), "
                    "2) ~/.alibabacloud/credentials file, or "
                    "3) ECS RAM role"
                )

            return oss.credentials.Credentials(
                access_key_id=creds.access_key_id,
                access_key_secret=creds.access_key_secret,
                security_token=creds.security_token or "",
            )

    _AlibabaCloudCredentialsProvider = AlibabaCloudCredentialsProvider
    return AlibabaCloudCredentialsProvider


class OSSClient:
    """Client for interacting with Alibaba Cloud OSS."""

    def __init__(
        self,
        bucket_name: str,
        endpoint: str,
        prefix: str = "",
        *,
        credentials_provider: Any | None = None,
        access_key_id: str | None = None,
        access_key_secret: str | None = None,
        security_token: str | None = None,
    ):
        oss = _require_oss2()
        self.bucket_name = bucket_name
        self.endpoint = endpoint
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""

        if credentials_provider is not None:
            self._auth = oss.ProviderAuthV4(credentials_provider)
        elif access_key_id and access_key_secret:
            if security_token:
                self._auth = oss.StsAuth(
                    access_key_id, access_key_secret, security_token
                )
            else:
                self._auth = oss.Auth(access_key_id, access_key_secret)
        else:
            raise ValueError(
                "Either credentials_provider or (access_key_id + access_key_secret) must be provided"
            )

        self._bucket = oss.Bucket(self._auth, endpoint, bucket_name)

    @classmethod
    def from_env(
        cls,
        bucket_name: str | None = None,
        prefix: str | None = None,
    ) -> OSSClient:
        _require_oss2()
        env_bucket = os.getenv("DOJOZERO_OSS_BUCKET")
        endpoint = os.getenv("DOJOZERO_OSS_ENDPOINT")
        env_prefix = os.getenv("DOJOZERO_OSS_PREFIX", "")

        if not endpoint:
            raise ValueError("DOJOZERO_OSS_ENDPOINT environment variable not set")

        final_bucket = bucket_name or env_bucket
        if not final_bucket:
            raise ValueError("Bucket name not provided and DOJOZERO_OSS_BUCKET not set")

        final_prefix = prefix if prefix is not None else env_prefix

        ProviderCls = _credentials_provider_type()
        credentials_provider = ProviderCls()
        credentials_provider.get_credentials()

        return cls(
            bucket_name=final_bucket,
            endpoint=endpoint,
            prefix=final_prefix,
            credentials_provider=credentials_provider,
        )

    def _make_key(self, key: str) -> str:
        key = key.lstrip("/")
        return f"{self.prefix}{key}"

    def upload_file(self, local_path: str | Path, oss_key: str) -> str:
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
        local_dir = Path(local_dir)
        if not local_dir.is_dir():
            raise NotADirectoryError(f"Not a directory: {local_dir}")

        uploaded_keys: list[str] = []

        for file_path in local_dir.glob(pattern):
            if file_path.is_file():
                relative_path = file_path.relative_to(local_dir)
                oss_key = f"{oss_prefix.rstrip('/')}/{relative_path}"
                full_key = self.upload_file(file_path, oss_key)
                uploaded_keys.append(full_key)

        return uploaded_keys

    def file_exists(self, oss_key: str) -> bool:
        full_key = self._make_key(oss_key)
        return self._bucket.object_exists(full_key)

    def download_file(self, oss_key: str, local_path: str | Path) -> Path:
        full_key = self._make_key(oss_key)
        local_path = Path(local_path)

        local_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Downloading oss://%s/%s to %s", self.bucket_name, full_key, local_path
        )

        self._bucket.get_object_to_file(full_key, str(local_path))

        logger.info("Successfully downloaded %s", local_path)
        return local_path

    def list_files(self, oss_prefix: str = "", pattern: str = "*") -> list[str]:
        import fnmatch

        oss = _require_oss2()
        full_prefix = self._make_key(oss_prefix)
        matching_keys: list[str] = []

        for obj in oss.ObjectIterator(self._bucket, prefix=full_prefix):
            if self.prefix and obj.key.startswith(self.prefix):
                relative_key = obj.key[len(self.prefix) :]
            else:
                relative_key = obj.key

            if fnmatch.fnmatch(relative_key, pattern):
                matching_keys.append(relative_key)

        return sorted(matching_keys)


def upload_file(
    local_path: str | Path,
    oss_key: str,
    bucket_name: str | None = None,
    prefix: str | None = None,
) -> str:
    client = OSSClient.from_env(bucket_name=bucket_name, prefix=prefix)
    return client.upload_file(local_path, oss_key)


def upload_directory(
    local_dir: str | Path,
    oss_prefix: str,
    pattern: str = "*",
    bucket_name: str | None = None,
    prefix: str | None = None,
) -> list[str]:
    client = OSSClient.from_env(bucket_name=bucket_name, prefix=prefix)
    return client.upload_directory(local_dir, oss_prefix, pattern)
