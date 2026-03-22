"""Redis client for DojoZero sync service and arena server.

This module provides a unified Redis client for both:
- Sync Service: writes data from SLS to Redis
- Arena Server: reads data from Redis

Redis Data Structure:
    arena:version                    # Global version number (incremented on each sync)
    arena:meta:last_sync             # Last sync time (ISO string)
    arena:trials_list                # Trial ID list (JSON array)
    arena:trial_info                 # Trial metadata (Hash: trial_id -> JSON)
    arena:agent_info                 # Agent info (Hash: agent_id -> JSON)
    arena:spans:{trial_id}           # Spans per trial (JSON array)
    arena:hot:leaderboard            # Leaderboard (JSON array)
    arena:hot:leaderboard:{league}   # Per-league leaderboard
    arena:hot:agent_actions          # Recent agent actions (JSON array)
    arena:hot:agent_actions:{league} # Per-league agent actions
    arena:hot:stats                  # Stats (JSON object)
    arena:hot:stats:{league}         # Per-league stats
    arena:hot:games                  # Games (JSON object)
    arena:hot:games:{league}         # Per-league games
    arena:hot:live_trials            # Current live trial IDs (JSON array)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

LOGGER = logging.getLogger("dojozero.sync_service.redis")


def _make_json_serializable(obj: Any) -> Any:
    """Recursively convert an object to JSON-serializable form.

    Handles:
    - Pydantic models (via model_dump)
    - Objects with to_dict method
    - Dataclasses (via __dict__)
    - Nested dicts and lists
    - Datetime objects
    """
    if obj is None:
        return None

    # Handle Pydantic models
    if hasattr(obj, "model_dump"):
        return _make_json_serializable(obj.model_dump())

    # Handle objects with to_dict method
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return _make_json_serializable(obj.to_dict())

    # Handle dataclasses
    if hasattr(obj, "__dataclass_fields__"):
        return _make_json_serializable(
            {k: getattr(obj, k) for k in obj.__dataclass_fields__}
        )

    # Handle datetime objects
    if isinstance(obj, datetime):
        return obj.isoformat()

    # Handle dicts - recursively process values
    if isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}

    # Handle lists/tuples - recursively process items
    if isinstance(obj, (list, tuple)):
        return [_make_json_serializable(item) for item in obj]

    # Handle sets
    if isinstance(obj, set):
        return [_make_json_serializable(item) for item in obj]

    # Primitive types (str, int, float, bool, None) pass through
    return obj


# Default Redis URL
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

# Key prefix for all arena data
KEY_PREFIX = "arena:"

# TTL for cached data (7 days)
DEFAULT_TTL = 86400 * 7


@dataclass
class RedisClient:
    """Redis client for DojoZero.

    Provides methods for reading and writing arena data to Redis.
    Supports both sync service (write) and arena server (read) use cases.
    """

    redis_url: str | None = None
    prefix: str = KEY_PREFIX
    default_ttl: int = DEFAULT_TTL

    _client: Any = None  # redis.asyncio.Redis
    _connected: bool = False

    async def connect(self) -> bool:
        """Connect to Redis.

        Returns:
            True if connected successfully, False otherwise.
        """
        if self._connected:
            return True

        url = self.redis_url or os.getenv("DOJOZERO_REDIS_URL", DEFAULT_REDIS_URL)
        if not url:
            LOGGER.warning("Redis URL not configured")
            return False

        try:
            import redis.asyncio as redis

            self._client = redis.from_url(url, decode_responses=True)
            await self._client.ping()
            self._connected = True

            # Log connection info (hide password if any)
            safe_url = url.split("@")[-1] if "@" in url else url
            LOGGER.info("Connected to Redis: %s", safe_url)
            return True
        except ImportError:
            LOGGER.error(
                "redis package not installed. Install with: pip install 'dojozero[redis]'"
            )
            return False
        except Exception as e:
            LOGGER.error("Failed to connect to Redis: %s", e)
            return False

    async def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.close()
            self._client = None
            self._connected = False
            LOGGER.info("Redis connection closed")

    @property
    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self._connected

    # =========================================================================
    # Version Management
    # =========================================================================

    async def get_version(self) -> int:
        """Get current data version.

        Returns:
            Current version number, or 0 if not set.
        """
        if not self._connected:
            return 0
        try:
            version = await self._client.get(f"{self.prefix}version")
            return int(version) if version else 0
        except Exception as e:
            LOGGER.warning("Failed to get version: %s", e)
            return 0

    async def increment_version(self) -> int:
        """Increment and return new version.

        Returns:
            New version number.
        """
        if not self._connected:
            return 0
        try:
            return await self._client.incr(f"{self.prefix}version")
        except Exception as e:
            LOGGER.error("Failed to increment version: %s", e)
            return 0

    # =========================================================================
    # Sync Metadata
    # =========================================================================

    async def get_last_sync_time(self) -> datetime | None:
        """Get last sync time.

        Returns:
            Last sync time as datetime, or None if not set.
        """
        if not self._connected:
            return None
        try:
            value = await self._client.get(f"{self.prefix}meta:last_sync")
            if value:
                return datetime.fromisoformat(value)
            return None
        except Exception as e:
            LOGGER.warning("Failed to get last sync time: %s", e)
            return None

    async def set_last_sync_time(self, dt: datetime) -> None:
        """Set last sync time.

        Args:
            dt: Sync time to set.
        """
        if not self._connected:
            return
        try:
            await self._client.set(
                f"{self.prefix}meta:last_sync",
                dt.isoformat(),
            )
        except Exception as e:
            LOGGER.error("Failed to set last sync time: %s", e)

    def is_sync_time_valid(
        self, last_sync: datetime | None, max_age_days: int = 7
    ) -> bool:
        """Check if last sync time is valid (not too old).

        Args:
            last_sync: Last sync time to check.
            max_age_days: Maximum age in days.

        Returns:
            True if valid, False if None or too old.
        """
        if last_sync is None:
            return False
        age = datetime.now(timezone.utc) - last_sync
        return age < timedelta(days=max_age_days)

    # =========================================================================
    # Trials List
    # =========================================================================

    async def get_trials_list(self) -> list[str]:
        """Get list of trial IDs.

        Returns:
            List of trial IDs, or empty list if not cached.
        """
        if not self._connected:
            return []
        try:
            data = await self._client.get(f"{self.prefix}trials_list")
            return json.loads(data) if data else []
        except Exception as e:
            LOGGER.warning("Failed to get trials list: %s", e)
            return []

    async def set_trials_list(self, trial_ids: list[str]) -> None:
        """Set list of trial IDs.

        Args:
            trial_ids: List of trial IDs.
        """
        if not self._connected:
            return
        try:
            await self._client.setex(
                f"{self.prefix}trials_list",
                self.default_ttl,
                json.dumps(trial_ids),
            )
        except Exception as e:
            LOGGER.error("Failed to set trials list: %s", e)

    # =========================================================================
    # Trial Info (Hash)
    # =========================================================================

    async def get_trial_info(self, trial_id: str) -> dict[str, Any] | None:
        """Get trial info for a specific trial.

        Args:
            trial_id: Trial ID.

        Returns:
            Trial info dict, or None if not cached.
        """
        if not self._connected:
            return None
        try:
            data = await self._client.hget(f"{self.prefix}trial_info", trial_id)
            return json.loads(data) if data else None
        except Exception as e:
            LOGGER.warning("Failed to get trial info for %s: %s", trial_id, e)
            return None

    async def get_all_trial_info(self) -> dict[str, dict[str, Any]]:
        """Get all trial info.

        Returns:
            Dict mapping trial_id to trial info.
        """
        if not self._connected:
            return {}
        try:
            data = await self._client.hgetall(f"{self.prefix}trial_info")
            return {k: json.loads(v) for k, v in data.items()}
        except Exception as e:
            LOGGER.warning("Failed to get all trial info: %s", e)
            return {}

    async def set_trial_info(self, trial_id: str, info: dict[str, Any]) -> None:
        """Set trial info for a specific trial.

        Args:
            trial_id: Trial ID.
            info: Trial info dict.
        """
        if not self._connected:
            return
        try:
            serializable_info = _make_json_serializable(info)
            await self._client.hset(
                f"{self.prefix}trial_info",
                trial_id,
                json.dumps(serializable_info),
            )
        except Exception as e:
            LOGGER.error("Failed to set trial info for %s: %s", trial_id, e)

    async def set_all_trial_info(self, trial_info: dict[str, dict[str, Any]]) -> None:
        """Set all trial info (replaces existing).

        Args:
            trial_info: Dict mapping trial_id to trial info.
        """
        if not self._connected or not trial_info:
            return
        try:
            key = f"{self.prefix}trial_info"
            pipe = self._client.pipeline()
            pipe.delete(key)
            for tid, info in trial_info.items():
                serializable_info = _make_json_serializable(info)
                pipe.hset(key, tid, json.dumps(serializable_info))
            pipe.expire(key, self.default_ttl)
            await pipe.execute()
        except Exception as e:
            LOGGER.error("Failed to set all trial info: %s", e)

    # =========================================================================
    # Agent Info (Hash)
    # =========================================================================

    async def get_agent_info(self, agent_id: str) -> dict[str, Any] | None:
        """Get agent info for a specific agent.

        Args:
            agent_id: Agent ID.

        Returns:
            Agent info dict, or None if not cached.
        """
        if not self._connected:
            return None
        try:
            data = await self._client.hget(f"{self.prefix}agent_info", agent_id)
            return json.loads(data) if data else None
        except Exception as e:
            LOGGER.warning("Failed to get agent info for %s: %s", agent_id, e)
            return None

    async def get_all_agent_info(self) -> dict[str, dict[str, Any]]:
        """Get all agent info.

        Returns:
            Dict mapping agent_id to agent info.
        """
        if not self._connected:
            return {}
        try:
            data = await self._client.hgetall(f"{self.prefix}agent_info")
            return {k: json.loads(v) for k, v in data.items()}
        except Exception as e:
            LOGGER.warning("Failed to get all agent info: %s", e)
            return {}

    async def set_all_agent_info(self, agent_info: dict[str, dict[str, Any]]) -> None:
        """Set all agent info (replaces existing).

        Args:
            agent_info: Dict mapping agent_id to agent info.
        """
        if not self._connected or not agent_info:
            return
        try:
            key = f"{self.prefix}agent_info"
            pipe = self._client.pipeline()
            pipe.delete(key)
            for aid, info in agent_info.items():
                pipe.hset(key, aid, json.dumps(info))
            pipe.expire(key, self.default_ttl)
            await pipe.execute()
        except Exception as e:
            LOGGER.error("Failed to set all agent info: %s", e)

    # =========================================================================
    # Spans (per trial)
    # =========================================================================

    async def get_spans(self, trial_id: str) -> list[dict[str, Any]]:
        """Get spans for a specific trial.

        Args:
            trial_id: Trial ID.

        Returns:
            List of span dicts, or empty list if not cached.
        """
        if not self._connected:
            return []
        try:
            data = await self._client.get(f"{self.prefix}spans:{trial_id}")
            return json.loads(data) if data else []
        except Exception as e:
            LOGGER.warning("Failed to get spans for %s: %s", trial_id, e)
            return []

    async def set_spans(self, trial_id: str, spans: list[dict[str, Any]]) -> None:
        """Set spans for a specific trial.

        Args:
            trial_id: Trial ID.
            spans: List of span dicts.
        """
        if not self._connected:
            return
        try:
            await self._client.setex(
                f"{self.prefix}spans:{trial_id}",
                self.default_ttl,
                json.dumps(spans),
            )
        except Exception as e:
            LOGGER.error("Failed to set spans for %s: %s", trial_id, e)

    async def get_all_spans(
        self, trial_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Get spans for multiple trials.

        Args:
            trial_ids: List of trial IDs.

        Returns:
            Dict mapping trial_id to list of span dicts.
        """
        if not self._connected or not trial_ids:
            return {}
        try:
            pipe = self._client.pipeline()
            for tid in trial_ids:
                pipe.get(f"{self.prefix}spans:{tid}")
            results = await pipe.execute()

            spans_by_trial = {}
            for tid, data in zip(trial_ids, results):
                if data:
                    spans_by_trial[tid] = json.loads(data)
            return spans_by_trial
        except Exception as e:
            LOGGER.warning("Failed to get all spans: %s", e)
            return {}

    async def set_all_spans(
        self, spans_by_trial: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Set spans for multiple trials.

        Args:
            spans_by_trial: Dict mapping trial_id to list of span dicts.
        """
        if not self._connected or not spans_by_trial:
            return
        try:
            pipe = self._client.pipeline()
            for tid, spans in spans_by_trial.items():
                pipe.setex(
                    f"{self.prefix}spans:{tid}",
                    self.default_ttl,
                    json.dumps(spans),
                )
            await pipe.execute()
        except Exception as e:
            LOGGER.error("Failed to set all spans: %s", e)

    # =========================================================================
    # Hot Data (Leaderboard, Agent Actions, Stats, Games)
    # =========================================================================

    async def get_leaderboard(self, league: str | None = None) -> list[dict[str, Any]]:
        """Get leaderboard data.

        Args:
            league: Optional league filter (e.g., "NBA", "NFL").

        Returns:
            List of leaderboard entries.
        """
        if not self._connected:
            return []
        try:
            key = f"{self.prefix}hot:leaderboard"
            if league:
                key = f"{key}:{league.upper()}"
            data = await self._client.get(key)
            return json.loads(data) if data else []
        except Exception as e:
            LOGGER.warning("Failed to get leaderboard: %s", e)
            return []

    async def set_leaderboard(
        self, data: list[dict[str, Any]], league: str | None = None
    ) -> None:
        """Set leaderboard data.

        Args:
            data: List of leaderboard entries.
            league: Optional league filter.
        """
        if not self._connected:
            return
        try:
            key = f"{self.prefix}hot:leaderboard"
            if league:
                key = f"{key}:{league.upper()}"
            await self._client.setex(key, self.default_ttl, json.dumps(data))
        except Exception as e:
            LOGGER.error("Failed to set leaderboard: %s", e)

    async def get_agent_actions(
        self, league: str | None = None
    ) -> list[dict[str, Any]]:
        """Get agent actions data.

        Args:
            league: Optional league filter.

        Returns:
            List of agent action entries.
        """
        if not self._connected:
            return []
        try:
            key = f"{self.prefix}hot:agent_actions"
            if league:
                key = f"{key}:{league.upper()}"
            data = await self._client.get(key)
            return json.loads(data) if data else []
        except Exception as e:
            LOGGER.warning("Failed to get agent actions: %s", e)
            return []

    async def set_agent_actions(
        self, data: list[dict[str, Any]], league: str | None = None
    ) -> None:
        """Set agent actions data.

        Args:
            data: List of agent action entries.
            league: Optional league filter.
        """
        if not self._connected:
            return
        try:
            key = f"{self.prefix}hot:agent_actions"
            if league:
                key = f"{key}:{league.upper()}"
            await self._client.setex(key, self.default_ttl, json.dumps(data))
        except Exception as e:
            LOGGER.error("Failed to set agent actions: %s", e)

    async def get_stats(self, league: str | None = None) -> dict[str, Any] | None:
        """Get stats data.

        Args:
            league: Optional league filter.

        Returns:
            Stats dict, or None if not cached.
        """
        if not self._connected:
            return None
        try:
            key = f"{self.prefix}hot:stats"
            if league:
                key = f"{key}:{league.upper()}"
            data = await self._client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            LOGGER.warning("Failed to get stats: %s", e)
            return None

    async def set_stats(self, data: dict[str, Any], league: str | None = None) -> None:
        """Set stats data.

        Args:
            data: Stats dict.
            league: Optional league filter.
        """
        if not self._connected:
            return
        try:
            key = f"{self.prefix}hot:stats"
            if league:
                key = f"{key}:{league.upper()}"
            await self._client.setex(key, self.default_ttl, json.dumps(data))
        except Exception as e:
            LOGGER.error("Failed to set stats: %s", e)

    async def get_games(self, league: str | None = None) -> dict[str, Any] | None:
        """Get games data.

        Args:
            league: Optional league filter.

        Returns:
            Games dict, or None if not cached.
        """
        if not self._connected:
            return None
        try:
            key = f"{self.prefix}hot:games"
            if league:
                key = f"{key}:{league.upper()}"
            data = await self._client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            LOGGER.warning("Failed to get games: %s", e)
            return None

    async def set_games(self, data: dict[str, Any], league: str | None = None) -> None:
        """Set games data.

        Args:
            data: Games dict.
            league: Optional league filter.
        """
        if not self._connected:
            return
        try:
            key = f"{self.prefix}hot:games"
            if league:
                key = f"{key}:{league.upper()}"
            await self._client.setex(key, self.default_ttl, json.dumps(data))
        except Exception as e:
            LOGGER.error("Failed to set games: %s", e)

    async def get_live_trials(self) -> list[str]:
        """Get list of live trial IDs.

        Returns:
            List of live trial IDs.
        """
        if not self._connected:
            return []
        try:
            data = await self._client.get(f"{self.prefix}hot:live_trials")
            return json.loads(data) if data else []
        except Exception as e:
            LOGGER.warning("Failed to get live trials: %s", e)
            return []

    async def set_live_trials(self, trial_ids: list[str]) -> None:
        """Set list of live trial IDs.

        Args:
            trial_ids: List of live trial IDs.
        """
        if not self._connected:
            return
        try:
            await self._client.setex(
                f"{self.prefix}hot:live_trials",
                self.default_ttl,
                json.dumps(trial_ids),
            )
        except Exception as e:
            LOGGER.error("Failed to set live trials: %s", e)

    # =========================================================================
    # Batch Operations (for Sync Service)
    # =========================================================================

    async def sync_all_data(
        self,
        trials_list: list[str],
        trial_info: dict[str, dict[str, Any]],
        agent_info: dict[str, dict[str, Any]],
        spans_by_trial: dict[str, list[dict[str, Any]]],
        leaderboard: list[dict[str, Any]],
        leaderboard_by_league: dict[str, list[dict[str, Any]]],
        agent_actions: list[dict[str, Any]],
        agent_actions_by_league: dict[str, list[dict[str, Any]]],
        stats: dict[str, Any],
        stats_by_league: dict[str, dict[str, Any]],
        games: dict[str, Any],
        games_by_league: dict[str, dict[str, Any]],
        live_trials: list[str],
        sync_time: datetime,
    ) -> bool:
        """Sync all data to Redis atomically using pipeline.

        This is the main method used by Sync Service to write all data.
        Uses Redis pipeline for atomic operation.

        Returns:
            True if successful, False otherwise.
        """
        if not self._connected:
            return False

        try:
            pipe = self._client.pipeline()

            # Trials list
            pipe.setex(
                f"{self.prefix}trials_list",
                self.default_ttl,
                json.dumps(trials_list),
            )

            # Trial info (hash) - ensure nested objects are serializable
            trial_info_key = f"{self.prefix}trial_info"
            pipe.delete(trial_info_key)
            for tid, info in trial_info.items():
                serializable_info = _make_json_serializable(info)
                pipe.hset(trial_info_key, tid, json.dumps(serializable_info))
            if trial_info:
                pipe.expire(trial_info_key, self.default_ttl)

            # Agent info (hash) - ensure nested objects are serializable
            agent_info_key = f"{self.prefix}agent_info"
            pipe.delete(agent_info_key)
            for aid, info in agent_info.items():
                serializable_info = _make_json_serializable(info)
                pipe.hset(agent_info_key, aid, json.dumps(serializable_info))
            if agent_info:
                pipe.expire(agent_info_key, self.default_ttl)

            # Spans (per trial) - ensure nested objects are serializable
            for tid, spans in spans_by_trial.items():
                serializable_spans = _make_json_serializable(spans)
                pipe.setex(
                    f"{self.prefix}spans:{tid}",
                    self.default_ttl,
                    json.dumps(serializable_spans),
                )

            # Hot data - global (ensure all nested objects are serializable)
            pipe.setex(
                f"{self.prefix}hot:leaderboard",
                self.default_ttl,
                json.dumps(_make_json_serializable(leaderboard)),
            )
            pipe.setex(
                f"{self.prefix}hot:agent_actions",
                self.default_ttl,
                json.dumps(_make_json_serializable(agent_actions)),
            )
            pipe.setex(
                f"{self.prefix}hot:stats",
                self.default_ttl,
                json.dumps(_make_json_serializable(stats)),
            )
            pipe.setex(
                f"{self.prefix}hot:games",
                self.default_ttl,
                json.dumps(_make_json_serializable(games)),
            )
            pipe.setex(
                f"{self.prefix}hot:live_trials",
                self.default_ttl,
                json.dumps(live_trials),
            )

            # Hot data - per league (ensure all nested objects are serializable)
            for league, data in leaderboard_by_league.items():
                pipe.setex(
                    f"{self.prefix}hot:leaderboard:{league}",
                    self.default_ttl,
                    json.dumps(_make_json_serializable(data)),
                )
            for league, data in agent_actions_by_league.items():
                pipe.setex(
                    f"{self.prefix}hot:agent_actions:{league}",
                    self.default_ttl,
                    json.dumps(_make_json_serializable(data)),
                )
            for league, data in stats_by_league.items():
                pipe.setex(
                    f"{self.prefix}hot:stats:{league}",
                    self.default_ttl,
                    json.dumps(_make_json_serializable(data)),
                )
            for league, data in games_by_league.items():
                pipe.setex(
                    f"{self.prefix}hot:games:{league}",
                    self.default_ttl,
                    json.dumps(_make_json_serializable(data)),
                )

            # Increment version (this signals Arena Server to refresh)
            pipe.incr(f"{self.prefix}version")

            # Update last sync time (must be last for consistency)
            pipe.set(f"{self.prefix}meta:last_sync", sync_time.isoformat())

            await pipe.execute()
            LOGGER.info(
                "Synced all data to Redis: %d trials, %d agents, %d span sets",
                len(trials_list),
                len(agent_info),
                len(spans_by_trial),
            )
            return True
        except Exception as e:
            LOGGER.error("Failed to sync all data to Redis: %s", e)
            return False
