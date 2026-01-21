"""Alibaba Cloud credential provider for DojoZero.

This module provides a unified credential provider using the alibabacloud-credentials
SDK, which automatically handles credential discovery and refresh across all environments:

- Local development: .env file, ~/.alibabacloud/credentials file, or environment variables
- CI/CD: Environment variables
- ECS: Instance RAM role (automatic, no secrets needed)
- K8s (ACK): OIDC/RRSA (automatic, no secrets needed)

Environment variables (standard Alibaba Cloud names):
    ALIBABA_CLOUD_ACCESS_KEY_ID: Access key ID
    ALIBABA_CLOUD_ACCESS_KEY_SECRET: Access key secret
    ALIBABA_CLOUD_SECURITY_TOKEN: Security token (optional, for STS)
    ALIBABA_CLOUD_CREDENTIALS_FILE: Custom credentials file path
    ALIBABA_CLOUD_ROLE_ARN: RAM role ARN (for AssumeRole)
    ALIBABA_CLOUD_OIDC_PROVIDER_ARN: OIDC provider ARN (for K8s RRSA)
    ALIBABA_CLOUD_OIDC_TOKEN_FILE: OIDC token file path (for K8s RRSA)

Credentials file (~/.alibabacloud/credentials):
    [default]
    type = access_key
    access_key_id = LTAI5tXXXXXX
    access_key_secret = XXXXXXXX

Usage:
    from dojozero.core._credentials import get_credential_provider

    # Get credentials (auto-refreshes if using STS/RAM role)
    provider = get_credential_provider()
    creds = provider.get_credentials()
    print(creds.access_key_id)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, skip .env loading

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Credentials:
    """Alibaba Cloud credentials."""

    access_key_id: str
    access_key_secret: str
    security_token: str | None = None

    def is_valid(self) -> bool:
        """Check if credentials are present."""
        return bool(self.access_key_id and self.access_key_secret)


class CredentialProvider:
    """Credential provider using alibabacloud-credentials SDK.

    The SDK automatically handles:
    - Reading from environment variables
    - Reading from credentials file
    - Fetching from ECS instance metadata (RAM role)
    - Fetching from OIDC token (K8s RRSA)
    - Automatic token refresh for STS credentials
    """

    def __init__(self) -> None:
        """Initialize the credential provider."""
        try:
            from alibabacloud_credentials.client import Client

            self._client = Client()
            self._sdk_available = True

            # Detect credential type for logging
            cred_type = self._detect_credential_type()
            LOGGER.info("Credential provider initialized (type: %s)", cred_type)
        except ImportError:
            LOGGER.warning(
                "alibabacloud-credentials not installed. "
                "Install with: pip install alibabacloud-credentials"
            )
            self._client = None
            self._sdk_available = False

    def _detect_credential_type(self) -> str:
        """Detect which credential type the SDK is using."""
        # Check environment indicators
        if os.environ.get("ALIBABA_CLOUD_OIDC_TOKEN_FILE"):
            return "oidc"
        if os.environ.get("ALIBABA_CLOUD_ROLE_ARN"):
            return "assume_role"
        if os.environ.get("ALIBABA_CLOUD_ECS_METADATA_DISABLED") != "true":
            # Check if running on ECS by trying metadata
            try:
                import httpx

                resp = httpx.get(
                    "http://100.100.100.200/latest/meta-data/instance-id",
                    timeout=1,
                )
                if resp.status_code == 200:
                    return "ecs_ram_role"
            except Exception:
                pass
        if os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"):
            return "env_vars"
        if os.path.exists(
            os.path.expanduser("~/.alibabacloud/credentials")
        ) or os.environ.get("ALIBABA_CLOUD_CREDENTIALS_FILE"):
            return "credentials_file"
        return "unknown"

    def get_credentials(self) -> Credentials:
        """Get current credentials.

        The SDK handles automatic refresh for STS/RAM role credentials.

        Returns:
            Credentials object with access key, secret, and optional security token.

        Raises:
            RuntimeError: If SDK is not available and no fallback credentials found.
        """
        if self._sdk_available and self._client is not None:
            # Use get_credential() which returns a Credential object (new SDK API)
            cred = self._client.get_credential()
            return Credentials(
                access_key_id=cred.access_key_id or "",
                access_key_secret=cred.access_key_secret or "",
                security_token=cred.security_token,
            )

        # Fallback: try environment variables directly
        ak_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
        ak_secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
        token = os.environ.get("ALIBABA_CLOUD_SECURITY_TOKEN")

        return Credentials(
            access_key_id=ak_id,
            access_key_secret=ak_secret,
            security_token=token,
        )

    async def get_credentials_async(self) -> Credentials:
        """Async wrapper for get_credentials.

        The SDK is synchronous but fast for cached credentials,
        so this is just a convenience wrapper.
        """
        return self.get_credentials()

    def validate(self) -> tuple[bool, str]:
        """Validate that credentials are configured and accessible.

        Returns:
            Tuple of (is_valid, message)
        """
        if not self._sdk_available:
            return False, "alibabacloud-credentials SDK not installed"

        try:
            creds = self.get_credentials()
            if not creds.is_valid():
                return False, "No credentials found"
            return True, f"Credentials valid (ak_id: {creds.access_key_id[:8]}...)"
        except Exception as e:
            return False, f"Credential error: {e}"


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_provider: CredentialProvider | None = None


def get_credential_provider() -> CredentialProvider:
    """Get the global credential provider instance.

    Creates the provider on first call (lazy initialization).

    Returns:
        CredentialProvider instance
    """
    global _provider
    if _provider is None:
        _provider = CredentialProvider()
    return _provider


def init_credential_provider(validate: bool = False) -> CredentialProvider:
    """Initialize the global credential provider.

    Args:
        validate: If True, validate credentials are accessible.

    Returns:
        CredentialProvider instance

    Raises:
        ValueError: If validate=True and credentials are not valid.
    """
    provider = get_credential_provider()

    if validate:
        is_valid, message = provider.validate()
        if not is_valid:
            raise ValueError(f"Credential validation failed: {message}")
        LOGGER.info("Credentials validated: %s", message)

    return provider


__all__ = [
    "CredentialProvider",
    "Credentials",
    "get_credential_provider",
    "init_credential_provider",
]
