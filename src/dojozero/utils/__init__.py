"""DojoZero utility modules.

OSS helpers load only when accessed (requires ``dojozero[alicloud]``).
"""

from typing import TYPE_CHECKING

from dojozero.utils.time import (
    utc_iso_to_local,
    utc_iso_to_local_date,
    utc_to_local_date,
    utc_to_us_date,
)

__all__ = [
    "OSSClient",
    "upload_file",
    "upload_directory",
    "utc_iso_to_local",
    "utc_iso_to_local_date",
    "utc_to_local_date",
    "utc_to_us_date",
]

if TYPE_CHECKING:
    from dojozero.utils.oss import OSSClient, upload_directory, upload_file


def __getattr__(name: str):
    if name in ("OSSClient", "upload_file", "upload_directory"):
        from dojozero.utils import oss as _oss

        return getattr(_oss, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
