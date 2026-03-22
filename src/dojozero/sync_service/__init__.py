"""Sync Service for DojoZero.

This module implements the SLS -> Redis synchronization service.
It runs as an independent process that:
1. Pulls data from SLS (Alibaba Cloud Log Service)
2. Writes to Redis for fast access by Arena Server

The Sync Service maintains the same data refresh logic as BackgroundRefresher,
but outputs to Redis instead of in-memory cache.
"""

from dojozero.sync_service._redis_client import RedisClient
from dojozero.sync_service._sync import SyncService

__all__ = ["RedisClient", "SyncService"]
