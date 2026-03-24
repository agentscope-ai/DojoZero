"""SLS to Redis sync service.

This module implements the core sync logic that:
1. Pulls data from SLS (Alibaba Cloud Log Service)
2. Processes data using existing arena_server utilities
3. Writes to Redis for fast access by Arena Server

The sync logic mirrors BackgroundRefresher but outputs to Redis.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from dojozero.arena_server._cache import (
    CACHEABLE_LEAGUES,
    CacheConfig,
    DEFAULT_CACHE_CONFIG,
    LandingPageCache,
)
from dojozero.arena_server._utils import (
    _compute_leaderboard_from_spans,
    _compute_stats,
    _extract_agent_actions_from_spans,
    _extract_games_from_trials,
    _extract_trial_info_from_spans,
    _filter_trials_by_league,
    TRIAL_INFO_OPERATION_NAMES,
)
from dojozero.core._tracing import SpanData, TraceReader, create_trace_reader
from dojozero.sync_service._redis_client import RedisClient

LOGGER = logging.getLogger("dojozero.sync_service")


@dataclass
class SyncService:
    """SLS to Redis sync service.

    Continuously syncs data from SLS to Redis:
    1. Initial sync: full fetch from lookback period
    2. Periodic sync: incremental fetch every refresh_interval seconds

    The sync logic mirrors BackgroundRefresher._refresh_all() but writes to Redis
    instead of in-memory cache.
    """

    # Core dependencies
    trace_reader: TraceReader
    redis_client: RedisClient
    config: CacheConfig = field(default_factory=lambda: DEFAULT_CACHE_CONFIG)

    # Internal state
    _running: bool = False
    _spans_by_trial: dict[str, list[SpanData]] = field(default_factory=dict)
    _last_sync_time: datetime | None = None

    # Temporary in-memory cache for processing (not persisted)
    _temp_cache: LandingPageCache | None = None

    @classmethod
    def from_env(cls) -> "SyncService":
        """Create SyncService from environment variables.

        Environment variables:
            DOJOZERO_REDIS_URL: Redis connection URL
            DOJOZERO_SYNC_INTERVAL: Sync interval in seconds (default: 5)
            DOJOZERO_LOOKBACK_DAYS: Lookback period in days (default: 90)
        """
        redis_url = os.getenv("DOJOZERO_REDIS_URL")
        sync_interval = float(os.getenv("DOJOZERO_SYNC_INTERVAL", "5"))
        lookback_days = int(os.getenv("DOJOZERO_LOOKBACK_DAYS", "90"))

        trace_reader = create_trace_reader(
            backend="sls",
            service_name=os.getenv("DOJOZERO_SERVICE_NAME", "dojozero"),
        )

        redis_client = RedisClient(redis_url=redis_url)

        config = CacheConfig(
            refresh_interval=sync_interval,
            trials_lookback_days=lookback_days,
        )

        return cls(
            trace_reader=trace_reader,
            redis_client=redis_client,
            config=config,
        )

    async def start(self) -> None:
        """Start the sync service.

        This is a blocking call that runs the sync loop until stopped.
        """
        if self._running:
            return

        self._running = True
        LOGGER.info("SyncService: Starting...")

        # Connect to Redis
        if not await self.redis_client.connect():
            LOGGER.error("SyncService: Failed to connect to Redis, exiting")
            self._running = False
            return

        # Initialize temporary cache for data processing
        self._temp_cache = LandingPageCache(config=self.config)

        try:
            # Check last sync time from Redis
            self._last_sync_time = await self.redis_client.get_last_sync_time()

            # Always do a full sync on startup since _spans_by_trial is empty.
            # Even if _last_sync_time is valid, we need historical spans to
            # compute leaderboard correctly (broker.final_stats from completed games).
            if self._last_sync_time is not None:
                LOGGER.info(
                    "SyncService: Found last sync time: %s, but doing full sync (empty cache)",
                    self._last_sync_time,
                )
            else:
                LOGGER.info("SyncService: No last sync time, starting full sync")
            # Always do a full sync on startup
            await self._sync_once(is_initial=True)

            LOGGER.info("SyncService: Initial sync complete, entering refresh loop")

            # Enter periodic refresh loop
            while self._running:
                await asyncio.sleep(self.config.refresh_interval)
                if not self._running:
                    break
                try:
                    await self._sync_once(is_initial=False)
                except Exception as e:
                    LOGGER.error("SyncService: Sync failed: %s", e)

        except asyncio.CancelledError:
            LOGGER.info("SyncService: Cancelled")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the sync service."""
        if not self._running:
            return

        self._running = False
        LOGGER.info("SyncService: Stopping...")

        # Close Redis connection
        await self.redis_client.close()

        # Close trace reader if it has a close method
        close_fn = getattr(self.trace_reader, "close", None)
        if close_fn is not None:
            await close_fn()

        LOGGER.info("SyncService: Stopped")

    async def _sync_once(self, *, is_initial: bool = False) -> None:
        """Perform a single sync cycle.

        Args:
            is_initial: True for full sync (first run), False for incremental.
        """
        prefix = "Full" if is_initial else "Incremental"
        LOGGER.info("SyncService: [1/8] %s sync starting...", prefix)

        if self._temp_cache is None:
            self._temp_cache = LandingPageCache(config=self.config)

        # 1. Get trial list
        start_dt = datetime.now(timezone.utc) - timedelta(
            days=self.config.trials_lookback_days
        )
        trial_ids = await self.trace_reader.list_trials(
            start_time=start_dt, limit=self.config.trials_limit
        )

        if not trial_ids:
            LOGGER.info("SyncService: No trials found, skipping sync")
            return

        LOGGER.info("SyncService: [2/8] Trial list fetched (%d trials)", len(trial_ids))

        # 2. Fetch spans - full or incremental
        now = datetime.now(timezone.utc)
        if is_initial or self._last_sync_time is None:
            # Full fetch with lookback buffer
            spans_start_dt = now - timedelta(days=self.config.trials_lookback_days + 1)
            all_spans = await self.trace_reader.get_all_spans(start_time=spans_start_dt)
            LOGGER.info(
                "SyncService: [3/8] Full fetch complete (%d spans)", len(all_spans)
            )

            # Reset cached data on full fetch
            self._spans_by_trial.clear()
            self._temp_cache.clear_agent_info()
        else:
            # Incremental fetch since last sync
            all_spans = await self.trace_reader.get_all_spans(
                start_time=self._last_sync_time
            )
            LOGGER.info(
                "SyncService: [3/8] Incremental fetch complete (%d new spans)",
                len(all_spans),
            )

        # 3. Group spans by trial_id
        for span in all_spans:
            trial_id = span.trace_id
            if trial_id not in self._spans_by_trial:
                self._spans_by_trial[trial_id] = []
            self._spans_by_trial[trial_id].append(span)

        # Prune stale trials
        trial_ids_set = set(trial_ids)
        stale_trial_ids = [
            tid for tid in self._spans_by_trial if tid not in trial_ids_set
        ]
        for tid in stale_trial_ids:
            del self._spans_by_trial[tid]

        LOGGER.info(
            "SyncService: [4/8] Grouped spans into %d trials (pruned %d stale)",
            len(self._spans_by_trial),
            len(stale_trial_ids),
        )

        # 4. Extract agent info
        added = self._temp_cache.update_agent_info_from_spans(all_spans)
        LOGGER.info(
            "SyncService: [5/8] Agent info updated (%d new, %d total)",
            added,
            self._temp_cache.get_total_agents(),
        )

        # 5. Process trial_info
        await self._process_trial_info(trial_ids)
        LOGGER.info("SyncService: [6/8] Trial info processed")

        # 6. Refresh stats, games, leaderboard, agent_actions
        await self._refresh_aggregated_data(trial_ids)
        LOGGER.info("SyncService: [7/8] Aggregated data refreshed")

        # 7. Write everything to Redis
        await self._write_to_redis(trial_ids, now)
        LOGGER.info("SyncService: [8/8] %s sync complete", prefix)

        # Update last sync time
        self._last_sync_time = now

    async def _process_trial_info(self, trial_ids: list[str]) -> None:
        """Process trial info from cached spans."""
        if self._temp_cache is None:
            return

        for trial_id in trial_ids:
            # Skip if already processed and not running
            cached_info = self._temp_cache.get_trial_info(trial_id)
            if cached_info is not None:
                phase = cached_info.get("phase", "")
                if phase in ("completed", "stopped"):
                    continue  # Skip completed trials

            spans = self._spans_by_trial.get(trial_id, [])
            relevant_spans = [
                s for s in spans if s.operation_name in TRIAL_INFO_OPERATION_NAMES
            ]

            if relevant_spans:
                trial_info = _extract_trial_info_from_spans(relevant_spans)
                self._temp_cache.set_trial_info(trial_id, trial_info)
            else:
                self._temp_cache.set_trial_info(
                    trial_id, {"phase": "unknown", "metadata": {}}
                )

    async def _refresh_aggregated_data(self, trial_ids: list[str]) -> None:
        """Refresh stats, games, leaderboard, and agent actions."""
        if self._temp_cache is None:
            return

        agent_info_cache = self._temp_cache.get_all_agent_info()

        # Stats (global + per-league)
        stats = await _compute_stats(
            self.trace_reader, trial_ids, self._temp_cache, self._spans_by_trial
        )
        self._temp_cache.set_stats(stats, league=None)

        for league in CACHEABLE_LEAGUES:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self._temp_cache
            )
            league_stats = await _compute_stats(
                self.trace_reader, filtered_ids, self._temp_cache, self._spans_by_trial
            )
            self._temp_cache.set_stats(league_stats, league=league)

        # Games (global + per-league)
        games = await _extract_games_from_trials(
            self.trace_reader, trial_ids, self._temp_cache
        )
        self._temp_cache.set_games(games, league=None)

        for league in CACHEABLE_LEAGUES:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self._temp_cache
            )
            league_games = await _extract_games_from_trials(
                self.trace_reader, filtered_ids, self._temp_cache
            )
            self._temp_cache.set_games(league_games, league=league)

        # Leaderboard (global + per-league)
        leaderboard = _compute_leaderboard_from_spans(
            self._spans_by_trial, agent_info_cache, trial_ids
        )
        self._temp_cache.set_leaderboard(leaderboard, league=None)

        for league in CACHEABLE_LEAGUES:
            league_ids = [
                tid for tid in trial_ids if self._trial_matches_league(tid, league)
            ]
            league_leaderboard = _compute_leaderboard_from_spans(
                self._spans_by_trial, agent_info_cache, league_ids
            )
            self._temp_cache.set_leaderboard(league_leaderboard, league=league)

        # Agent actions (global + per-league)
        actions = _extract_agent_actions_from_spans(
            self._spans_by_trial,
            agent_info_cache,
            trial_ids,
            limit=self.config.agent_actions_limit,
            max_trials=self.config.agent_actions_max_trials,
        )
        self._temp_cache.set_agent_actions(actions, league=None)

        for league in CACHEABLE_LEAGUES:
            league_ids = [
                tid for tid in trial_ids if self._trial_matches_league(tid, league)
            ]
            league_actions = _extract_agent_actions_from_spans(
                self._spans_by_trial,
                agent_info_cache,
                league_ids,
                limit=self.config.agent_actions_limit,
                max_trials=self.config.agent_actions_max_trials,
            )
            self._temp_cache.set_agent_actions(league_actions, league=league)

    def _trial_matches_league(self, trial_id: str, league: str) -> bool:
        """Check if a trial matches a specific league."""
        if self._temp_cache is None:
            return False
        trial_info = self._temp_cache.get_trial_info(trial_id)
        if trial_info is None:
            return False
        metadata = trial_info.get("metadata", {})
        trial_league = metadata.get("sport_type", "")
        return trial_league.upper() == league.upper()

    async def _write_to_redis(self, trial_ids: list[str], sync_time: datetime) -> None:
        """Write all cached data to Redis."""
        if self._temp_cache is None:
            return

        # Prepare data for Redis
        # Trial info
        trial_info: dict[str, dict[str, Any]] = {}
        for tid in trial_ids:
            info = self._temp_cache.get_trial_info(tid)
            if info:
                trial_info[tid] = info

        # Agent info (convert to dict)
        agent_info: dict[str, dict[str, Any]] = {}
        for aid, info in self._temp_cache.get_all_agent_info().items():
            if hasattr(info, "model_dump"):
                agent_info[aid] = info.model_dump()
            else:
                agent_info[aid] = dict(info) if info else {}

        # Spans by trial (convert SpanData to dict)
        spans_by_trial: dict[str, list[dict[str, Any]]] = {}
        for tid, spans in self._spans_by_trial.items():
            spans_by_trial[tid] = [s.to_dict() for s in spans]

        # Leaderboard (convert to dict)
        leaderboard = self._temp_cache.get_leaderboard(league=None) or []
        leaderboard_data = [
            e.model_dump() if hasattr(e, "model_dump") else dict(e) for e in leaderboard
        ]

        leaderboard_by_league: dict[str, list[dict[str, Any]]] = {}
        for league in CACHEABLE_LEAGUES:
            lb = self._temp_cache.get_leaderboard(league=league) or []
            leaderboard_by_league[league] = [
                e.model_dump() if hasattr(e, "model_dump") else dict(e) for e in lb
            ]

        # Agent actions (convert to dict)
        actions = self._temp_cache.get_agent_actions(league=None) or []
        actions_data = [
            a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in actions
        ]

        actions_by_league: dict[str, list[dict[str, Any]]] = {}
        for league in CACHEABLE_LEAGUES:
            aa = self._temp_cache.get_agent_actions(league=league) or []
            actions_by_league[league] = [
                a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in aa
            ]

        # Stats (convert to dict)
        stats = self._temp_cache.get_stats(league=None)
        stats_data = (
            stats.model_dump() if stats and hasattr(stats, "model_dump") else {}
        )

        stats_by_league: dict[str, dict[str, Any]] = {}
        for league in CACHEABLE_LEAGUES:
            s = self._temp_cache.get_stats(league=league)
            stats_by_league[league] = (
                s.model_dump() if s and hasattr(s, "model_dump") else {}
            )

        # Games (convert to dict)
        games = self._temp_cache.get_games(league=None)
        games_data = (
            games.model_dump() if games and hasattr(games, "model_dump") else {}
        )

        games_by_league: dict[str, dict[str, Any]] = {}
        for league in CACHEABLE_LEAGUES:
            g = self._temp_cache.get_games(league=league)
            games_by_league[league] = (
                g.model_dump() if g and hasattr(g, "model_dump") else {}
            )

        # Live trials
        live_trials = self._temp_cache.get_live_trial_ids()

        # Write all to Redis
        success = await self.redis_client.sync_all_data(
            trials_list=trial_ids,
            trial_info=trial_info,
            agent_info=agent_info,
            spans_by_trial=spans_by_trial,
            leaderboard=leaderboard_data,
            leaderboard_by_league=leaderboard_by_league,
            agent_actions=actions_data,
            agent_actions_by_league=actions_by_league,
            stats=stats_data,
            stats_by_league=stats_by_league,
            games=games_data,
            games_by_league=games_by_league,
            live_trials=live_trials,
            sync_time=sync_time,
        )

        if success:
            LOGGER.info(
                "SyncService: Wrote to Redis: %d trials, %d agents, %d span sets",
                len(trial_ids),
                len(agent_info),
                len(spans_by_trial),
            )
        else:
            LOGGER.error("SyncService: Failed to write to Redis")
