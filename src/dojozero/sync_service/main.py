#!/usr/bin/env python3
"""Entry point for the SLS to Redis sync service.

Usage:
    # Run directly
    dojo0 sync-service
    (uv run dojo0 sync-service)

    # Or using python command
    uv run python -m dojozero.sync_service.main

Environment variables:
    DOJOZERO_REDIS_URL: Redis connection URL
        Example: redis://r-xxx.redis.singapore.rds.aliyuncs.com:6379/0
    DOJOZERO_SYNC_INTERVAL: Sync interval in seconds (default: 5)
    DOJOZERO_LOOKBACK_DAYS: Lookback period in days (default: 90)
    DOJOZERO_SERVICE_NAME: Service name for SLS (default: dojozero)

SLS credentials are configured via:
    - ALIBABA_CLOUD_ACCESS_KEY_ID
    - ALIBABA_CLOUD_ACCESS_KEY_SECRET
    - Or ECS RAM role (automatic on ECS instances)
"""

import asyncio
import logging
import os
import signal
import sys

from dojozero.sync_service._sync import SyncService


def setup_logging() -> None:
    """Configure logging for the sync service."""
    level = os.getenv("DOJOZERO_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def main() -> int:
    """Run the sync service."""
    setup_logging()
    logger = logging.getLogger("dojozero.sync_service.main")

    # Validate required environment variables
    redis_url = os.getenv("DOJOZERO_REDIS_URL")
    if not redis_url:
        logger.error("DOJOZERO_REDIS_URL environment variable is required")
        return 1

    logger.info("Starting SLS to Redis sync service...")
    logger.info(
        "Redis URL: %s", redis_url.split("@")[-1] if "@" in redis_url else redis_url
    )
    logger.info("Sync interval: %s seconds", os.getenv("DOJOZERO_SYNC_INTERVAL", "5"))
    logger.info("Lookback days: %s", os.getenv("DOJOZERO_LOOKBACK_DAYS", "90"))

    # Create sync service
    service = SyncService.from_env()

    # Setup signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_signal(signum: int, frame: object) -> None:
        logger.info("Received signal %d, initiating shutdown...", signum)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Start service in background task
    service_task = asyncio.create_task(service.start())

    # Wait for shutdown signal
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass

    # Stop service
    logger.info("Shutting down...")
    await service.stop()
    service_task.cancel()

    try:
        await service_task
    except asyncio.CancelledError:
        pass

    logger.info("Sync service stopped")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
