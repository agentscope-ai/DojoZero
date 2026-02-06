#!/usr/bin/env python3
"""Delete logs from an SLS logstore.

This script deletes logs from a specified SLS logstore. By default, it deletes
all logs, but you can specify a time range or filter by trace ID.

IMPORTANT: This operation is destructive and cannot be undone.

Usage:
    # Delete all logs (interactive confirmation required)
    uv run python tools/delete_sls_logs.py

    # Delete logs from a specific time range
    uv run python tools/delete_sls_logs.py --from "2024-01-01 00:00:00" --to "2024-01-31 23:59:59"

    # Delete logs by trace ID (e.g., a specific trial)
    uv run python tools/delete_sls_logs.py --trace-id abc123-def456

    # Skip confirmation (use with caution)
    uv run python tools/delete_sls_logs.py --force

    # Override logstore name
    uv run python tools/delete_sls_logs.py --logstore my-other-logstore
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def load_env() -> None:
    """Load environment variables from .env file if it exists."""
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        logger.debug("Loading .env from %s", env_file)
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value


def parse_datetime(dt_str: str) -> int:
    """Parse datetime string to Unix timestamp.

    Supports formats:
    - "2024-01-15 10:30:00" (local time)
    - "2024-01-15T10:30:00Z" (UTC)
    - Unix timestamp as string
    """
    # Try Unix timestamp first
    try:
        return int(dt_str)
    except ValueError:
        pass

    # Try ISO format with Z suffix
    if dt_str.endswith("Z"):
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return int(dt.timestamp())

    # Try common datetime formats
    for fmt in [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
    ]:
        try:
            dt = datetime.strptime(dt_str, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue

    raise ValueError(f"Cannot parse datetime: {dt_str}")


def delete_logs(
    endpoint: str,
    project: str,
    logstore: str,
    from_time: int,
    to_time: int,
    ak_id: str,
    ak_secret: str,
    security_token: str | None = None,
    query: str = "*",
) -> bool:
    """Delete logs from SLS logstore.

    Args:
        endpoint: SLS endpoint (e.g., cn-hangzhou.log.aliyuncs.com)
        project: SLS project name
        logstore: SLS logstore name
        from_time: Start time (Unix timestamp)
        to_time: End time (Unix timestamp)
        ak_id: Alibaba Cloud Access Key ID
        ak_secret: Alibaba Cloud Access Key Secret
        security_token: Optional STS security token
        query: SLS query to filter logs (default: "*" for all logs)

    Returns:
        True if successful, False otherwise
    """
    try:
        from aliyun.log import DeleteLogsRequest, LogClient
    except ImportError:
        logger.error(
            "aliyun-log-python-sdk not installed. Run: uv pip install aliyun-log-python-sdk"
        )
        return False

    try:
        # Create client
        if security_token:
            client = LogClient(endpoint, ak_id, ak_secret, security_token)
        else:
            client = LogClient(endpoint, ak_id, ak_secret)

        logger.info(
            "Deleting logs from logstore '%s' in project '%s'", logstore, project
        )
        logger.info(
            "Time range: %s to %s",
            datetime.fromtimestamp(from_time).isoformat(),
            datetime.fromtimestamp(to_time).isoformat(),
        )
        logger.info("Query: %s", query)

        # Create delete request
        request = DeleteLogsRequest(
            project=project,
            logstore=logstore,
            fromTime=from_time,
            toTime=to_time,
            topic="",  # Empty string means all topics
            query=query,
        )
        client.delete_logs(request)

        logger.info("Delete request submitted successfully")
        logger.info(
            "Note: Log deletion is asynchronous and may take some time to complete"
        )
        return True

    except Exception as e:
        logger.error("Failed to delete logs: %s: %s", type(e).__name__, e)
        return False


def get_log_count(
    endpoint: str,
    project: str,
    logstore: str,
    from_time: int,
    to_time: int,
    ak_id: str,
    ak_secret: str,
    security_token: str | None = None,
    query: str = "*",
) -> int | None:
    """Get approximate log count in the time range."""
    try:
        from aliyun.log import GetLogsRequest, LogClient
    except ImportError:
        return None

    try:
        if security_token:
            client = LogClient(endpoint, ak_id, ak_secret, security_token)
        else:
            client = LogClient(endpoint, ak_id, ak_secret)

        # Use count query with optional filter
        count_query = f"{query} | select count(1) as cnt"
        request = GetLogsRequest(
            project=project,
            logstore=logstore,
            fromTime=from_time,
            toTime=to_time,
            query=count_query,
        )
        response = client.get_logs(request)

        if response is not None:
            for log in response.get_logs():
                if "cnt" in log:
                    return int(log["cnt"])

        return 0
    except Exception as e:
        logger.debug("Could not get log count: %s", e)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete logs from an SLS logstore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--from",
        dest="from_time",
        help="Start time (e.g., '2024-01-01 00:00:00' or Unix timestamp). Default: 30 days ago",
    )
    parser.add_argument(
        "--to",
        dest="to_time",
        help="End time (e.g., '2024-12-31 23:59:59' or Unix timestamp). Default: now",
    )
    parser.add_argument(
        "--logstore",
        help="Override SLS logstore name (default: from DOJOZERO_SLS_LOGSTORE)",
    )
    parser.add_argument(
        "--project",
        help="Override SLS project name (default: from DOJOZERO_SLS_PROJECT)",
    )
    parser.add_argument(
        "--endpoint",
        help="Override SLS endpoint (default: from DOJOZERO_SLS_ENDPOINT)",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Delete all logs (extends time range to cover all possible logs)",
    )
    parser.add_argument(
        "--trace-id",
        help="Delete logs for a specific trace ID (e.g., a trial ID)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s: %(message)s",
    )

    load_env()

    # Get configuration from environment
    endpoint = args.endpoint or os.environ.get("DOJOZERO_SLS_ENDPOINT", "")
    project = args.project or os.environ.get("DOJOZERO_SLS_PROJECT", "")
    logstore = args.logstore or os.environ.get("DOJOZERO_SLS_LOGSTORE", "")
    ak_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    ak_secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")

    # Validate configuration
    missing = []
    if not endpoint:
        missing.append("DOJOZERO_SLS_ENDPOINT")
    if not project:
        missing.append("DOJOZERO_SLS_PROJECT")
    if not logstore:
        missing.append("DOJOZERO_SLS_LOGSTORE")
    if not ak_id:
        missing.append("ALIBABA_CLOUD_ACCESS_KEY_ID")
    if not ak_secret:
        missing.append("ALIBABA_CLOUD_ACCESS_KEY_SECRET")

    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        return 1

    # Parse time range
    now = int(time.time())

    if args.all:
        # For --all, use a very wide time range
        # SLS typically retains logs for up to 3650 days (10 years)
        from_time = 0  # Unix epoch
        to_time = now + 86400  # Now + 1 day buffer
    else:
        # Default: last 30 days
        if args.from_time:
            try:
                from_time = parse_datetime(args.from_time)
            except ValueError as e:
                logger.error("Invalid --from time: %s", e)
                return 1
        else:
            from_time = now - (30 * 24 * 60 * 60)  # 30 days ago

        if args.to_time:
            try:
                to_time = parse_datetime(args.to_time)
            except ValueError as e:
                logger.error("Invalid --to time: %s", e)
                return 1
        else:
            to_time = now

    if from_time >= to_time:
        logger.error("--from time must be before --to time")
        return 1

    # Build query
    if args.trace_id:
        query = f'_trace_id: "{args.trace_id}"'
    else:
        query = "*"

    # Display what will be deleted
    print()
    print("=" * 60)
    print("SLS Log Deletion")
    print("=" * 60)
    print(f"  Endpoint:  {endpoint}")
    print(f"  Project:   {project}")
    print(f"  Logstore:  {logstore}")
    print(f"  From:      {datetime.fromtimestamp(from_time).isoformat()}")
    print(f"  To:        {datetime.fromtimestamp(to_time).isoformat()}")
    if args.trace_id:
        print(f"  Trace ID:  {args.trace_id}")
    print(f"  Query:     {query}")
    print()

    # Try to get log count
    count = get_log_count(
        endpoint, project, logstore, from_time, to_time, ak_id, ak_secret, query=query
    )
    if count is not None:
        print(f"  Estimated logs to delete: {count:,}")
        print()

    print("WARNING: This operation is DESTRUCTIVE and CANNOT be undone!")
    print()

    # Confirmation
    if not args.force:
        try:
            response = input("Type 'DELETE' to confirm: ")
            if response != "DELETE":
                print("Aborted.")
                return 1
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return 1

    print()

    # Perform deletion
    if delete_logs(
        endpoint, project, logstore, from_time, to_time, ak_id, ak_secret, query=query
    ):
        print()
        print("Log deletion request submitted successfully.")
        print(
            "Note: Deletion is asynchronous and may take several minutes to complete."
        )
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
