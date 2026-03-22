"""Runtime checks for optional Alibaba Cloud-related wheels.

Base install (`pip install dojozero`) omits OSS/SLS/credentials SDKs.
Use `pip install 'dojozero[alicloud]'` for those features.
"""

from __future__ import annotations

INSTALL_ALICLOUD = "pip install 'dojozero[alicloud]'"


def ensure_alibabacloud_credentials() -> None:
    """Raise ImportError if alibabacloud-credentials is not installed."""
    try:
        from alibabacloud_credentials.client import Client  # noqa: F401
    except ImportError as e:
        raise ImportError(
            f"Missing alibabacloud-credentials (needed for SLS trace export). "
            f"Install with: {INSTALL_ALICLOUD}"
        ) from e


def ensure_oss2() -> None:
    """Raise ImportError if oss2 is not installed."""
    try:
        import oss2  # noqa: F401
    except ImportError as e:
        raise ImportError(
            f"Missing oss2 (needed for OSS backup and oss:// paths). "
            f"Install with: {INSTALL_ALICLOUD}"
        ) from e


INSTALL_REDIS = "pip install 'dojozero[redis]'"


def ensure_redis() -> None:
    """Raise ImportError if redis is not installed."""
    try:
        import redis  # noqa: F401
    except ImportError as e:
        raise ImportError(
            f"Missing redis package (needed for sync-service). Install with: {INSTALL_REDIS}"
        ) from e
