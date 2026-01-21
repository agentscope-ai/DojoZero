#!/usr/bin/env python3
"""Test Alibaba Cloud OSS access with credentials.

Required environment variables (in .env or exported):
    # Credentials (RAM user AccessKey)
    ALIBABA_CLOUD_ACCESS_KEY_ID=xxx
    ALIBABA_CLOUD_ACCESS_KEY_SECRET=xxx

    # OSS config
    DOJOZERO_OSS_ENDPOINT=oss-cn-wulanchabu.aliyuncs.com
    DOJOZERO_OSS_BUCKET=dojozero-store
    DOJOZERO_OSS_PREFIX=data/  # optional

Usage:
    python tools/test_alicloud_access.py
    python tools/test_alicloud_access.py --prefix data/nba/
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def load_env() -> None:
    """Load .env file via dotenv."""
    from dotenv import load_dotenv

    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        print(f"Loaded env from {env_file}")


def test_credentials() -> bool:
    """Test if credentials are configured."""
    print("\n=== Checking Credentials ===")

    ak_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    ak_secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")

    if ak_id and ak_secret:
        print(f"AccessKey ID: {ak_id[:8]}...{ak_id[-4:]}")
        print(f"AccessKey Secret: {'*' * 16}")
        return True
    else:
        print("ERROR: Missing credentials")
        print("  Set ALIBABA_CLOUD_ACCESS_KEY_ID and ALIBABA_CLOUD_ACCESS_KEY_SECRET")
        return False


def test_oss_config() -> bool:
    """Test if OSS config is set."""
    print("\n=== Checking OSS Config ===")

    endpoint = os.environ.get("DOJOZERO_OSS_ENDPOINT", "")
    bucket = os.environ.get("DOJOZERO_OSS_BUCKET", "")
    prefix = os.environ.get("DOJOZERO_OSS_PREFIX", "")

    print(f"Endpoint: {endpoint or '(not set)'}")
    print(f"Bucket: {bucket or '(not set)'}")
    print(f"Prefix: {prefix or '(not set)'}")

    if endpoint and bucket:
        return True
    else:
        print("ERROR: Missing DOJOZERO_OSS_ENDPOINT or DOJOZERO_OSS_BUCKET")
        return False


def test_oss_list(prefix: str | None = None) -> bool:
    """Test OSS list operation."""
    print("\n=== Testing OSS List ===")

    try:
        import oss2
    except ImportError:
        print("ERROR: oss2 not installed. Run: uv pip install oss2")
        return False

    ak_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    ak_secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
    endpoint = os.environ.get("DOJOZERO_OSS_ENDPOINT", "")
    bucket_name = os.environ.get("DOJOZERO_OSS_BUCKET", "")
    env_prefix = os.environ.get("DOJOZERO_OSS_PREFIX", "")

    list_prefix = prefix or env_prefix

    try:
        auth = oss2.Auth(ak_id, ak_secret)
        bucket = oss2.Bucket(auth, endpoint, bucket_name)

        print(f"Listing objects with prefix: '{list_prefix}'")
        print("-" * 40)

        count = 0
        for obj in oss2.ObjectIterator(bucket, prefix=list_prefix, max_keys=10):
            print(f"  {obj.key} ({obj.size} bytes)")
            count += 1

        if count == 0:
            print("  (no objects found)")
        else:
            print("-" * 40)
            print(f"Found {count} object(s) (limited to 10)")

        print("\nOSS access OK")
        return True

    except oss2.exceptions.AccessDenied as e:
        print("ERROR: Access denied - check RAM permissions")
        print(f"  {e}")
        return False
    except oss2.exceptions.NoSuchBucket as e:
        print(f"ERROR: Bucket not found - {bucket_name}")
        print(f"  {e}")
        return False
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Alibaba Cloud OSS access")
    parser.add_argument("--prefix", help="Override prefix for listing")
    args = parser.parse_args()

    load_env()

    if not test_credentials():
        return 1

    if not test_oss_config():
        return 1

    if not test_oss_list(args.prefix):
        return 1

    print("\n=== All tests passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
