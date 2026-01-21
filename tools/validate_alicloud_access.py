#!/usr/bin/env python3
"""Validate Alibaba Cloud OSS/SLS access with credentials.

Required environment variables (in .env or exported):
    # Credentials (RAM user AccessKey)
    ALIBABA_CLOUD_ACCESS_KEY_ID=xxx
    ALIBABA_CLOUD_ACCESS_KEY_SECRET=xxx

    # OSS config
    DOJOZERO_OSS_ENDPOINT=oss-cn-wulanchabu.aliyuncs.com
    DOJOZERO_OSS_BUCKET=dojozero-store
    DOJOZERO_OSS_PREFIX=data/  # optional

    # SLS config (for tracing)
    DOJOZERO_SLS_ENDPOINT=cn-wulanchabu.log.aliyuncs.com
    DOJOZERO_SLS_PROJECT=log-service-1228139055781573-cn-wulanchabu
    DOJOZERO_SLS_LOGSTORE=dojozero-traces

Usage:
    python tools/validate_alicloud_access.py
    python tools/validate_alicloud_access.py --prefix data/nba/
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger(__name__)


def load_env() -> None:
    """Load .env file via dotenv."""
    from dotenv import load_dotenv

    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        logger.info("Loaded env from %s", env_file)


def validate_credentials() -> bool:
    """Validate if credentials are configured."""
    logger.info("=== Checking Credentials ===")

    ak_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    ak_secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")

    if ak_id and ak_secret:
        logger.info("AccessKey ID: %s...%s", ak_id[:8], ak_id[-4:])
        logger.info("AccessKey Secret: %s", "*" * 16)
        return True
    else:
        logger.error("Missing credentials")
        logger.error(
            "  Set ALIBABA_CLOUD_ACCESS_KEY_ID and ALIBABA_CLOUD_ACCESS_KEY_SECRET"
        )
        return False


def validate_oss_config() -> bool:
    """Validate if OSS config is set."""
    logger.info("=== Checking OSS Config ===")

    endpoint = os.environ.get("DOJOZERO_OSS_ENDPOINT", "")
    bucket = os.environ.get("DOJOZERO_OSS_BUCKET", "")
    prefix = os.environ.get("DOJOZERO_OSS_PREFIX", "")

    logger.info("Endpoint: %s", endpoint or "(not set)")
    logger.info("Bucket: %s", bucket or "(not set)")
    logger.info("Prefix: %s", prefix or "(not set)")

    if endpoint and bucket:
        return True
    else:
        logger.error("Missing DOJOZERO_OSS_ENDPOINT or DOJOZERO_OSS_BUCKET")
        return False


def validate_oss_list(prefix: str | None = None) -> bool:
    """Validate OSS list operation."""
    logger.info("=== Validating OSS List ===")

    try:
        import oss2
    except ImportError:
        logger.error("oss2 not installed. Run: uv pip install oss2")
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

        logger.info("Listing objects with prefix: '%s'", list_prefix)

        count = 0
        for obj in oss2.ObjectIterator(bucket, prefix=list_prefix, max_keys=10):
            logger.info("  %s (%d bytes)", obj.key, obj.size)
            count += 1

        if count == 0:
            logger.info("  (no objects found)")
        else:
            logger.info("Found %d object(s) (limited to 10)", count)

        logger.info("OSS access OK")
        return True

    except oss2.exceptions.AccessDenied as e:
        logger.error("Access denied - check RAM permissions: %s", e)
        return False
    except oss2.exceptions.NoSuchBucket as e:
        logger.error("Bucket not found - %s: %s", bucket_name, e)
        return False
    except Exception as e:
        logger.error("%s: %s", type(e).__name__, e)
        return False


def validate_sls_config() -> bool:
    """Validate if SLS config is set."""
    logger.info("=== Checking SLS Config ===")

    endpoint = os.environ.get("DOJOZERO_SLS_ENDPOINT", "")
    project = os.environ.get("DOJOZERO_SLS_PROJECT", "")
    logstore = os.environ.get("DOJOZERO_SLS_LOGSTORE", "")

    logger.info("Endpoint: %s", endpoint or "(not set)")
    logger.info("Project: %s", project or "(not set)")
    logger.info("Logstore: %s", logstore or "(not set)")

    if endpoint and project and logstore:
        return True
    else:
        logger.warning("Missing SLS config (optional, for tracing)")
        return False


def validate_sls_query() -> bool:
    """Validate SLS query operation."""
    logger.info("=== Validating SLS Query ===")

    try:
        from aliyun.log import LogClient
    except ImportError:
        logger.error(
            "aliyun-log-python-sdk not installed. Run: uv pip install aliyun-log-python-sdk"
        )
        return False

    ak_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    ak_secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
    endpoint = os.environ.get("DOJOZERO_SLS_ENDPOINT", "")
    project = os.environ.get("DOJOZERO_SLS_PROJECT", "")
    logstore = os.environ.get("DOJOZERO_SLS_LOGSTORE", "")

    try:
        import time

        client = LogClient(endpoint, ak_id, ak_secret)

        # Query last 15 minutes
        to_time = int(time.time())
        from_time = to_time - 900

        logger.info("Querying logstore '%s' in project '%s'", logstore, project)
        logger.info("Time range: last 15 minutes")

        # Simple query to test access
        response = client.get_log(
            project,
            logstore,
            from_time,
            to_time,
            query="* | select count(*) as cnt",
        )

        # Get count from response
        if response is None:
            logger.info("  (no response from SLS)")
        else:
            logs = response.get_logs()
            if logs:
                cnt = logs[0].get_contents().get("cnt", "0")
                logger.info("Log count (last 15min): %s", cnt)
            else:
                logger.info("  (no logs found)")

        logger.info("SLS access OK")
        return True

    except Exception as e:
        logger.error("%s: %s", type(e).__name__, e)
        return False


def validate_sls_write() -> bool:
    """Validate SLS write operation."""
    logger.info("=== Validating SLS Write ===")

    try:
        from aliyun.log import LogClient, LogItem, PutLogsRequest
    except ImportError:
        logger.error("aliyun-log-python-sdk not installed")
        return False

    ak_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    ak_secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
    endpoint = os.environ.get("DOJOZERO_SLS_ENDPOINT", "")
    project = os.environ.get("DOJOZERO_SLS_PROJECT", "")
    logstore = os.environ.get("DOJOZERO_SLS_LOGSTORE", "")

    try:
        import time

        client = LogClient(endpoint, ak_id, ak_secret)

        # Create a test log item
        log_item = LogItem()
        log_item.set_time(int(time.time()))
        log_item.set_contents(
            [
                ("source", "validate_alicloud_access.py"),
                ("message", "Test log entry from DojoZero"),
                ("level", "INFO"),
                ("test_id", f"test-{int(time.time())}"),
            ]
        )

        # Create put logs request
        request = PutLogsRequest(
            project=project,
            logstore=logstore,
            topic="test",
            source="dojozero-test",
            logitems=[log_item],
        )

        logger.info("Writing test log to '%s' in project '%s'", logstore, project)
        client.put_logs(request)
        logger.info("Log written successfully")
        logger.info("SLS write OK")
        return True

    except Exception as e:
        logger.error("%s: %s", type(e).__name__, e)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Alibaba Cloud OSS/SLS access"
    )
    parser.add_argument("--prefix", help="Override prefix for OSS listing")
    parser.add_argument("--skip-oss", action="store_true", help="Skip OSS validation")
    parser.add_argument("--skip-sls", action="store_true", help="Skip SLS validation")
    parser.add_argument(
        "--write", action="store_true", help="Test SLS write (writes a test log)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
    )

    load_env()

    if not validate_credentials():
        return 1

    failed = False

    # OSS validation
    if not args.skip_oss:
        if not validate_oss_config():
            failed = True
        elif not validate_oss_list(args.prefix):
            failed = True

    # SLS validation
    if not args.skip_sls:
        if validate_sls_config():  # SLS is optional
            if args.write:
                if not validate_sls_write():
                    failed = True
            if not validate_sls_query():
                failed = True

    if failed:
        logger.error("=== Some validations failed ===")
        return 1

    logger.info("=== All validations passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
