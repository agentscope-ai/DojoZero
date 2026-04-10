"""Redis reader for Arena Server.

This module provides a reader that loads cached data from Redis
into the in-memory LandingPageCache used by Arena Server.

The Arena Server uses this to:
1. Load initial data from Redis on startup (fast, ~1-2 seconds)
2. Periodically check for updates and refresh hot data
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime

from dojozero.arena_server._cache import (
    CACHEABLE_LEAGUES,
    LEADERBOARD_PERIODS,
    LandingPageCache,
)
from dojozero.arena_server._models import BetRecord, GamesResponse, StatsResponse
from dojozero.betting import AgentInfo
from dojozero.core import AgentAction, LeaderboardEntry
from dojozero.core._tracing import SpanData
from dojozero.sync_service._redis_client import RedisClient

LOGGER = logging.getLogger("dojozero.arena_server.redis_reader")


@dataclass
class RedisReader:
    """Redis reader for Arena Server.

    Reads data from Redis (written by Sync Service) and populates
    the in-memory LandingPageCache.
    """

    redis_client: RedisClient
    cache: LandingPageCache

    # Track last seen version for change detection
    _last_version: int = 0

    @classmethod
    def from_env(cls, cache: LandingPageCache) -> "RedisReader":
        """Create RedisReader from environment variables."""
        redis_url = os.getenv("DOJOZERO_REDIS_URL")
        redis_client = RedisClient(redis_url=redis_url)
        return cls(redis_client=redis_client, cache=cache)

    async def connect(self) -> bool:
        """Connect to Redis.

        Returns:
            True if connected successfully.
        """
        return await self.redis_client.connect()

    async def close(self) -> None:
        """Close Redis connection."""
        await self.redis_client.close()

    @property
    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self.redis_client.is_connected

    async def get_version(self) -> int:
        """Get current data version from Redis."""
        return await self.redis_client.get_version()

    async def has_updates(self) -> bool:
        """Check if there are updates since last check.

        Returns:
            True if version has changed.
        """
        current_version = await self.get_version()
        if current_version != self._last_version:
            self._last_version = current_version
            return True
        return False

    async def load_all(self) -> bool:
        """Load all data from Redis into cache.

        This is called on startup to populate the initial cache.

        Returns:
            True if data was loaded successfully.
        """
        if not self.redis_client.is_connected:
            return False

        try:
            # Load trials list
            trials_list = await self.redis_client.get_trials_list()
            if trials_list:
                self.cache.set_trials_list(trials_list)
                LOGGER.info("Loaded %d trials from Redis", len(trials_list))

            # Load trial info
            trial_info = await self.redis_client.get_all_trial_info()
            for tid, info in trial_info.items():
                self.cache.set_trial_info(tid, info)
            LOGGER.info("Loaded trial info for %d trials", len(trial_info))

            # Load agent info
            agent_info = await self.redis_client.get_all_agent_info()
            for aid, info in agent_info.items():
                try:
                    self.cache._agent_info[aid] = AgentInfo.model_validate(info)
                except Exception as e:
                    LOGGER.warning("Failed to parse agent info for %s: %s", aid, e)
            LOGGER.info("Loaded %d agent info entries", len(agent_info))

            # Load hot data
            await self._load_hot_data()

            # Update version
            self._last_version = await self.get_version()

            LOGGER.info("Loaded all data from Redis (version %d)", self._last_version)
            return True

        except Exception as e:
            LOGGER.error("Failed to load data from Redis: %s", e)
            return False

    async def load_hot_data(self) -> bool:
        """Load only hot data (leaderboard, agent_actions, stats, games).

        This is called periodically to refresh frequently-changing data.

        Returns:
            True if data was loaded successfully.
        """
        if not self.redis_client.is_connected:
            return False

        try:
            await self._load_hot_data()
            self._last_version = await self.get_version()
            return True
        except Exception as e:
            LOGGER.error("Failed to load hot data from Redis: %s", e)
            return False

    async def _load_hot_data(self) -> None:
        """Internal method to load hot data."""
        # Leaderboard (global + per-league)
        leaderboard_data = await self.redis_client.get_leaderboard(league=None)
        if leaderboard_data:
            leaderboard = [LeaderboardEntry.model_validate(e) for e in leaderboard_data]
            self.cache.set_leaderboard(leaderboard, league=None)

        for league in CACHEABLE_LEAGUES:
            lb_data = await self.redis_client.get_leaderboard(league=league)
            if lb_data:
                lb = [LeaderboardEntry.model_validate(e) for e in lb_data]
                self.cache.set_leaderboard(lb, league=league)

        # Period leaderboards (global + per-league)
        for period in LEADERBOARD_PERIODS:
            p_data = await self.redis_client.get_leaderboard(period=period)
            if p_data:
                p_lb = [LeaderboardEntry.model_validate(e) for e in p_data]
                self.cache.set_leaderboard(p_lb, period=period)
            for league in CACHEABLE_LEAGUES:
                lp_data = await self.redis_client.get_leaderboard(
                    league=league,
                    period=period,
                )
                if lp_data:
                    lp_lb = [LeaderboardEntry.model_validate(e) for e in lp_data]
                    self.cache.set_leaderboard(lp_lb, league=league, period=period)

        # Agent actions (global + per-league)
        actions_data = await self.redis_client.get_agent_actions(league=None)
        if actions_data:
            actions = [AgentAction.model_validate(a) for a in actions_data]
            self.cache.set_agent_actions(actions, league=None)

        for league in CACHEABLE_LEAGUES:
            aa_data = await self.redis_client.get_agent_actions(league=league)
            if aa_data:
                aa = [AgentAction.model_validate(a) for a in aa_data]
                self.cache.set_agent_actions(aa, league=league)

        # Stats (global + per-league)
        stats_data = await self.redis_client.get_stats(league=None)
        if stats_data:
            stats = StatsResponse.model_validate(stats_data)
            self.cache.set_stats(stats, league=None)

        for league in CACHEABLE_LEAGUES:
            s_data = await self.redis_client.get_stats(league=league)
            if s_data:
                s = StatsResponse.model_validate(s_data)
                self.cache.set_stats(s, league=league)

        # Games (global + per-league)
        games_data = await self.redis_client.get_games(league=None)
        if games_data:
            games = GamesResponse.model_validate(games_data)
            self.cache.set_games(games, league=None)

        for league in CACHEABLE_LEAGUES:
            g_data = await self.redis_client.get_games(league=league)
            if g_data:
                g = GamesResponse.model_validate(g_data)
                self.cache.set_games(g, league=league)

        # Agent bets index
        bets_index_data = await self.redis_client.get_agent_bets_index()
        if bets_index_data:
            agent_bets_index: dict[str, list[BetRecord]] = {}
            for aid, bets_list in bets_index_data.items():
                agent_bets_index[aid] = [BetRecord.model_validate(b) for b in bets_list]
            self.cache.set_agent_bets_index(agent_bets_index)

        # Live trials are derived from trial_info, not stored separately
        # Redis provides them for convenience but cache derives from trial_info
        await self.redis_client.get_live_trials()

    async def load_spans_for_trial(self, trial_id: str) -> list[SpanData]:
        """Load spans for a specific trial from Redis.

        This is called on-demand when WebSocket clients connect.

        Args:
            trial_id: Trial ID to load spans for.

        Returns:
            List of SpanData objects.
        """
        if not self.redis_client.is_connected:
            return []

        try:
            spans_data = await self.redis_client.get_spans(trial_id)
            return [SpanData.from_dict(s) for s in spans_data]
        except Exception as e:
            LOGGER.warning("Failed to load spans for %s: %s", trial_id, e)
            return []

    async def get_last_sync_time(self) -> datetime | None:
        """Get last sync time from Redis.

        Returns:
            Last sync time, or None if not available.
        """
        return await self.redis_client.get_last_sync_time()
