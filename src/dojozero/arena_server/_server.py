"""Arena Server for DojoZero.

This module implements the Arena Server which is responsible for:
- Reading traces from Trace Store (Jaeger or SLS)
- Pushing OTel spans to browsers via WebSocket
- Serving React static files (optional, for production)
- Providing landing page data with caching

The Arena Server is a read-only service that only queries the trace store (Jaeger or SLS).
It does not communicate with the Dashboard Server directly.

Endpoints:
- GET  /api/trials                    - List trials with phase/metadata
- GET  /api/trials/{trial_id}         - Get trial info and spans
- POST /api/trials/{trial_id}/replay  - Get all replay data for a completed trial
- GET  /api/landing                   - Landing page data (games, stats, actions)
- GET  /api/stats                     - Real-time stats (games, wagered, etc.)
- GET  /api/games                     - All games (live, upcoming, completed)
- GET  /api/leaderboard               - Agent rankings by winnings
- GET  /api/agent-actions             - Recent agent actions
- WS   /ws/trials/{trial_id}/stream   - Real-time span streaming (supports pause/resume)
- WS   /ws/trials/{trial_id}/replay   - Replay completed trial (supports pause/resume/speed)

Filtering:
    Most endpoints support optional `league` query parameter for filtering by sport:
    - ?league=NBA  - Filter to NBA games only
    - ?league=NFL  - Filter to NFL games only
    - (omit)       - Return all leagues

    Supported endpoints: /api/landing, /api/stats, /api/games, /api/leaderboard, /api/agent-actions

    Per-league results are cached separately for leagues in CACHEABLE_LEAGUES (NBA, NFL).
    To add a new league, update CACHEABLE_LEAGUES in the code.

Configuration:
    dojo0 arena --trace-backend sls
    dojo0 arena --trace-backend jaeger --trace-query-endpoint http://localhost:16686
    # Use --service-name to specify the service name for both Jaeger and SLS backends
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

if TYPE_CHECKING:
    from dojozero.arena_server._config import ArenaServerConfig
from pydantic import BaseModel

from dojozero.arena_server._cache import (
    CACHEABLE_LEAGUES,
    CacheConfig,
    DEFAULT_CACHE_CONFIG,
    LandingPageCache,
    ReplayCache,
    ReplayMetaInfo,
)

from dojozero.arena_server._models import (
    BetSummary,
    GamesResponse,
    StatsResponse,
    WSReplayStatusMessage,
    WSSnapshotMessage,
    WSSpanMessage,
    WSTrialEndedMessage,
)
from dojozero.betting._models import (  # noqa: F401 - Required for model_rebuild()
    AgentInfo,  # For BetSummary
    AgentResponseMessage,  # For AgentAction
)
from dojozero.arena_server._utils import (
    _extract_trial_info_from_traces,
    _extract_trial_info_from_spans,
    _filter_trials_by_league,
    _extract_games_from_trials,
    _extract_agent_actions,
    _extract_agent_actions_from_spans,
    _compute_stats,
    _compute_leaderboard,
    _compute_leaderboard_from_spans,
    _load_replay_data,
    TRIAL_INFO_OPERATION_NAMES,
)
from dojozero.core._models import (
    AgentAction,
    LeaderboardEntry,
    TrialLifecycleSpan,
    deserialize_span,
    serialize_span_for_ws,
)
from dojozero.core._tracing import (
    SpanData,
    TraceReader,
    create_trace_reader,
)

# Rebuild Pydantic models to resolve forward references
# This must happen after imports to avoid circular import issues
AgentAction.model_rebuild()
LeaderboardEntry.model_rebuild()
BetSummary.model_rebuild()


# ============================================================================
# Category Filter
# ============================================================================


@dataclass(frozen=True)
class CategoryFilter:
    """Filter items by category.

    Generic filter that can be used for:
    - Replay filtering (REST and WebSocket)
    - Real-time stream filtering
    - Frontend query parameters

    Examples:
        # Include only play and game_update categories
        filter = CategoryFilter.from_query("play,game_update")

        # Exclude heartbeat and status categories
        filter = CategoryFilter.from_query("heartbeat,status", mode="exclude")

        # From JSON command (WebSocket)
        filter = CategoryFilter.from_list(["play", "game_update"])
    """

    categories: frozenset[str]  # Categories to filter
    mode: Literal["include", "exclude"] = "include"

    def matches(self, category: str) -> bool:
        """Check if a category matches the filter.

        Returns True if the category should be included in output.
        """
        if not self.categories:
            return True  # Empty filter = include all

        if self.mode == "include":
            return category in self.categories
        else:  # exclude
            return category not in self.categories

    def filter_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter a list of serialized items by category."""
        if not self.categories:
            return items
        return [item for item in items if self.matches(item.get("category", ""))]

    def filter_item(self, item: dict[str, Any]) -> bool:
        """Check if a single item should be included."""
        return self.matches(item.get("category", ""))

    @classmethod
    def from_query(
        cls,
        categories: str | None,
        mode: str = "include",
    ) -> "CategoryFilter":
        """Create filter from query parameter string.

        Args:
            categories: Comma-separated list of categories (e.g., "play,game_update")
            mode: "include" or "exclude"

        Returns:
            CategoryFilter instance
        """
        if not categories:
            return cls(categories=frozenset())

        cat_set = frozenset(c.strip() for c in categories.split(",") if c.strip())
        filter_mode: Literal["include", "exclude"] = (
            "exclude" if mode == "exclude" else "include"
        )
        return cls(categories=cat_set, mode=filter_mode)

    @classmethod
    def from_list(
        cls,
        categories: list[str] | None,
        mode: str = "include",
    ) -> "CategoryFilter":
        """Create filter from list (e.g., from JSON command).

        Args:
            categories: List of categories
            mode: "include" or "exclude"
        """
        if not categories:
            return cls(categories=frozenset())

        filter_mode: Literal["include", "exclude"] = (
            "exclude" if mode == "exclude" else "include"
        )
        return cls(categories=frozenset(categories), mode=filter_mode)


LOGGER = logging.getLogger("dojozero.arena_server")


class WSMessageType:
    SNAPSHOT = "snapshot"
    SPAN = "span"
    TRIAL_ENDED = "trial_ended"
    HEARTBEAT = "heartbeat"


@dataclass
class SpanBroadcaster:
    """Manages WebSocket clients and broadcasts spans by trial_id."""

    _clients: dict[str, set[WebSocket]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def subscribe(self, trial_id: str, websocket: WebSocket) -> None:
        """Add a WebSocket client to a trial's subscriber list."""
        async with self._lock:
            if trial_id not in self._clients:
                self._clients[trial_id] = set()
            self._clients[trial_id].add(websocket)
        LOGGER.debug(
            "Client subscribed to trial '%s' (total: %d)",
            trial_id,
            len(self._clients.get(trial_id, set())),
        )

    async def unsubscribe(self, trial_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket client from a trial's subscriber list."""
        async with self._lock:
            if trial_id in self._clients:
                self._clients[trial_id].discard(websocket)
                if not self._clients[trial_id]:
                    del self._clients[trial_id]
        LOGGER.debug("Client unsubscribed from trial '%s'", trial_id)

    async def broadcast_span(self, trial_id: str, span: SpanData) -> None:
        """Broadcast a span to all clients subscribed to a trial.

        Deserializes the raw SpanData into a typed model and sends
        a WSSpanMessage to clients. Unrecognized spans are silently dropped.
        """
        typed = deserialize_span(span)
        if typed is None:
            return
        ws_payload = serialize_span_for_ws(typed)
        message = WSSpanMessage(
            trial_id=trial_id,
            timestamp=datetime.now(timezone.utc),
            category=ws_payload.get("category", ""),
            data=ws_payload.get("data", {}),
        )
        await self._send_to_trial(trial_id, message)

    async def broadcast_trial_ended(self, trial_id: str) -> None:
        """Notify all clients that a trial has ended."""
        message = WSTrialEndedMessage(
            trial_id=trial_id,
            timestamp=datetime.now(timezone.utc),
        )
        await self._send_to_trial(trial_id, message)

    async def send_snapshot(
        self,
        trial_id: str,
        websocket: WebSocket,
        spans: list[SpanData],
    ) -> None:
        """Send a snapshot of recent spans to a specific client.

        Deserializes each raw SpanData into a typed model and sends
        a WSSnapshotMessage with all items.
        """
        LOGGER.info(
            "send_snapshot: trial=%s, span_count=%d",
            trial_id,
            len(spans),
        )
        items = []
        unrecognized_ops: list[str] = []
        for span in spans:
            LOGGER.debug(
                "Processing span: op='%s', tags_keys=%s",
                span.operation_name,
                list(span.tags.keys())[:5],
            )
            typed = deserialize_span(span)
            if typed is not None:
                items.append(serialize_span_for_ws(typed))
            else:
                unrecognized_ops.append(span.operation_name)

        if unrecognized_ops:
            LOGGER.warning(
                "Unrecognized spans (first 5): %s",
                unrecognized_ops[:5],
            )

        LOGGER.info(
            "send_snapshot: recognized %d/%d spans",
            len(items),
            len(spans),
        )
        message = WSSnapshotMessage(
            trial_id=trial_id,
            timestamp=datetime.now(timezone.utc),
            data={"items": items},
        )
        await self._send_to_client(websocket, message)

    async def _send_to_trial(self, trial_id: str, message: BaseModel) -> None:
        """Send a message to all clients subscribed to a trial."""
        async with self._lock:
            clients = list(self._clients.get(trial_id, set()))

        if not clients:
            return

        text = message.model_dump_json()
        disconnected: list[WebSocket] = []

        for websocket in clients:
            try:
                await websocket.send_text(text)
            except Exception:
                disconnected.append(websocket)

        for ws in disconnected:
            await self.unsubscribe(trial_id, ws)

    async def _send_to_client(
        self,
        websocket: WebSocket,
        message: BaseModel,
    ) -> None:
        """Send a message to a specific client."""
        try:
            text = message.model_dump_json()
            await websocket.send_text(text)
        except Exception as e:
            LOGGER.warning("Failed to send message to client: %s", e)


# =============================================================================
# Background Cache Refresher
# =============================================================================


@dataclass
class BackgroundRefresher:
    """Background cache refresh manager.

    Proactively refreshes all caches using a single consolidated refresh task.
    User requests only read from cache and never trigger fetches directly.

    Refresh Strategy (single task at config.refresh_interval):
    - Uses ONE get_all_spans() call per cycle (minimizes SLS queries)
    - Incremental fetch: only fetches spans since last refresh (not full lookback)
    - All caches refreshed together: trials_list, stats, games, leaderboard,
      agent_actions, trial_info, trial_details (global + per-league)
    - trial_details: Removed when trial completes
    - replay_cache: Preloaded at startup; refreshed on trial completion detection

    Startup:
    - Performs initial full refresh of all caches
    - Preloads replay data for completed trials
    - Blocks until complete (with timeout)
    """

    trace_reader: TraceReader
    cache: LandingPageCache
    replay_cache: ReplayCache
    config: CacheConfig = field(default_factory=lambda: DEFAULT_CACHE_CONFIG)

    # Background tasks
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)
    _running: bool = False
    _initial_refresh_done: asyncio.Event = field(default_factory=asyncio.Event)

    # Track previously known live trials (for detecting completion)
    _known_live_trials: set[str] = field(default_factory=set)

    # Cached spans for incremental refresh (populated on initial, merged on periodic)
    _spans_by_trial: dict[str, list[SpanData]] = field(default_factory=dict)
    _last_span_fetch_time: datetime | None = None

    async def start(self) -> None:
        """Start background refresh tasks.

        Performs initial refresh and then starts periodic refresh loops.
        """
        if self._running:
            return

        self._running = True
        LOGGER.info("BackgroundRefresher: Starting...")

        # Perform initial refresh (blocking)
        try:
            await self._refresh_all()
            self._initial_refresh_done.set()
            LOGGER.info("BackgroundRefresher: Initial refresh complete")
        except Exception as e:
            LOGGER.error("BackgroundRefresher: Initial refresh failed: %s", e)
            self._initial_refresh_done.set()  # Still set to unblock startup

        # Single consolidated refresh loop for ALL caches
        # Uses ONE get_all_spans() call per cycle instead of multiple queries
        self._tasks.append(
            asyncio.create_task(
                self._refresh_loop(
                    "all_data",
                    self._refresh_all_periodic,
                    self.config.refresh_interval,
                )
            )
        )

        LOGGER.info("BackgroundRefresher: Started %d refresh tasks", len(self._tasks))

    async def stop(self) -> None:
        """Stop all background refresh tasks."""
        if not self._running:
            return

        self._running = False
        LOGGER.info("BackgroundRefresher: Stopping...")

        for task in self._tasks:
            task.cancel()

        # Wait for all tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()
        LOGGER.info("BackgroundRefresher: Stopped")

    async def wait_for_ready(self, timeout: float | None = None) -> None:
        """Wait for initial refresh to complete.

        Args:
            timeout: Max seconds to wait. Uses config.startup_timeout if None.
        """
        if timeout is None:
            timeout = self.config.startup_timeout

        try:
            await asyncio.wait_for(self._initial_refresh_done.wait(), timeout)
        except asyncio.TimeoutError:
            LOGGER.warning(
                "BackgroundRefresher: Startup timeout (%.1fs), continuing with partial cache",
                timeout,
            )

    async def _refresh_loop(
        self,
        name: str,
        refresh_fn: Any,
        interval: float,
    ) -> None:
        """Generic refresh loop that calls refresh_fn at interval."""
        while self._running:
            try:
                await asyncio.sleep(interval)
                if not self._running:
                    break
                await refresh_fn()
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.warning("BackgroundRefresher: %s refresh failed: %s", name, e)

    async def _refresh_all(self, *, is_initial: bool = True) -> None:
        """Refresh all caches using bulk fetch.

        For initial refresh: fetches all spans from lookback period.
        For periodic refresh: fetches only new spans since last fetch (incremental).

        Args:
            is_initial: True for startup refresh (full fetch, preloads all replay data),
                       False for periodic refresh (incremental fetch)
        """
        prefix = "Initial" if is_initial else "Periodic"
        LOGGER.info("BackgroundRefresher: [1/9] %s refresh starting...", prefix)

        # 1. Get trial list (lightweight list_trials query)
        start_dt = datetime.now(timezone.utc) - timedelta(
            days=self.config.trials_lookback_days
        )
        trial_ids = await self.trace_reader.list_trials(
            start_time=start_dt, limit=self.config.trials_limit
        )
        if trial_ids:
            self.cache.set_trials_list(trial_ids)
            LOGGER.info(
                "BackgroundRefresher: [2/9] Trial list refreshed (%d trials)",
                len(trial_ids),
            )

        if not trial_ids:
            LOGGER.info("BackgroundRefresher: No trials found, skipping refresh")
            return

        # 2. Fetch spans - full for initial, incremental for periodic
        now = datetime.now(timezone.utc)
        if is_initial or self._last_span_fetch_time is None:
            # Full fetch with lookback buffer
            spans_start_dt = now - timedelta(days=self.config.trials_lookback_days + 1)
            all_spans = await self.trace_reader.get_all_spans(start_time=spans_start_dt)
            LOGGER.info(
                "BackgroundRefresher: [3/9] Full fetch complete (%d spans, lookback=%d+1 days)",
                len(all_spans),
                self.config.trials_lookback_days,
            )

            # Reset cached data on full fetch
            self._spans_by_trial.clear()
            self.cache.clear_agent_info()
        else:
            # Incremental fetch since last refresh
            all_spans = await self.trace_reader.get_all_spans(
                start_time=self._last_span_fetch_time
            )
            LOGGER.info(
                "BackgroundRefresher: [3/9] Incremental fetch complete (%d new spans)",
                len(all_spans),
            )

        # Update last fetch time
        self._last_span_fetch_time = now

        # 3. Group new spans by trial_id and merge into cache
        new_span_count = 0
        for span in all_spans:
            trial_id = span.trace_id
            if trial_id not in self._spans_by_trial:
                self._spans_by_trial[trial_id] = []
            self._spans_by_trial[trial_id].append(span)
            new_span_count += 1

        # Prune trials that are no longer in the active trial list
        trial_ids_set = set(trial_ids)
        stale_trial_ids = [
            tid for tid in self._spans_by_trial if tid not in trial_ids_set
        ]
        for tid in stale_trial_ids:
            del self._spans_by_trial[tid]

        if stale_trial_ids:
            LOGGER.info(
                "BackgroundRefresher: [4/9] Merged %d spans, pruned %d stale trials (%d cached)",
                new_span_count,
                len(stale_trial_ids),
                len(self._spans_by_trial),
            )
        else:
            LOGGER.info(
                "BackgroundRefresher: [4/9] Merged %d spans into %d trials",
                new_span_count,
                len(self._spans_by_trial),
            )

        # 5. Extract agent info from new spans and merge into cache
        added = self.cache.update_agent_info_from_spans(all_spans)
        LOGGER.info(
            "BackgroundRefresher: [5/9] Agent info updated (%d new, %d total)",
            added,
            self.cache.get_total_agents(),
        )

        # 6. Process trial_info from cached spans
        await self._refresh_trial_info_from_spans(trial_ids, self._spans_by_trial)
        LOGGER.info("BackgroundRefresher: [6/9] Trial info refreshed")

        # 7. Refresh aggregated caches (these use trial_info cache, no SLS queries)
        await asyncio.gather(
            self._refresh_stats(),
            self._refresh_games(),
            return_exceptions=True,
        )
        LOGGER.info("BackgroundRefresher: [7/9] Stats and games refreshed")

        # 8. Refresh leaderboard and agent actions from cached spans
        agent_info_cache = self.cache.get_all_agent_info()
        self._refresh_leaderboard_from_spans(
            self._spans_by_trial, agent_info_cache, trial_ids
        )
        self._refresh_agent_actions_from_spans(
            self._spans_by_trial, agent_info_cache, trial_ids
        )
        LOGGER.info("BackgroundRefresher: [8/9] Leaderboard and actions refreshed")

        # 9. Refresh live trials and handle replay loading
        await self._refresh_live_trials_and_replay(
            self._spans_by_trial, is_initial=is_initial
        )

        LOGGER.info(
            "BackgroundRefresher: [9/9] %s refresh complete (%d new spans, %d cached trials)",
            prefix,
            len(all_spans),
            len(self._spans_by_trial),
        )

    async def _refresh_all_periodic(self) -> None:
        """Periodic refresh - delegates to _refresh_all with is_initial=False."""
        await self._refresh_all(is_initial=False)

    async def _refresh_live_trials_and_replay(
        self,
        spans_by_trial: dict[str, list[SpanData]],
        *,
        is_initial: bool,
    ) -> None:
        """Refresh live trial details and handle replay loading.

        Args:
            spans_by_trial: Pre-fetched spans grouped by trial_id
            is_initial: If True, preload all completed trials; if False, only newly completed
        """
        # Get current live trials from cache
        current_live = set(self.cache.get_live_trial_ids())

        # Detect completed trials (were live, now not)
        completed_trials = self._known_live_trials - current_live
        for trial_id in completed_trials:
            LOGGER.info(
                "BackgroundRefresher: Trial %s completed, removing from trial_details",
                trial_id,
            )
            self.cache.remove_trial_details(trial_id)
            # Load replay data for newly completed trial
            await self._load_replay_for_trial(trial_id)

        # Update known live trials
        self._known_live_trials = current_live

        # Process each live trial
        for trial_id in current_live:
            existing = self.cache.get_trial_details(trial_id)
            if existing is not None:
                existing_items = existing.get("items", [])
                max_ts = existing.get("max_timestamp", 0)
                is_completed = existing.get("is_completed", False)
                if is_completed:
                    continue  # Skip completed trials
            else:
                existing_items = []
                max_ts = 0

            spans = spans_by_trial.get(trial_id, [])
            if spans:
                try:
                    self._process_trial_details(trial_id, spans, existing_items, max_ts)
                except Exception as e:
                    LOGGER.warning(
                        "BackgroundRefresher: Failed to process trial_details[%s]: %s",
                        trial_id,
                        e,
                    )

        LOGGER.debug(
            "BackgroundRefresher: Refreshed %d live trials from pre-fetched spans",
            len(current_live),
        )

        # On initial refresh, preload replay for ALL completed trials
        if is_initial:
            await self._preload_replay_cache()

    async def _refresh_trial_info_from_spans(
        self,
        trial_ids: list[str],
        spans_by_trial: dict[str, list[SpanData]],
    ) -> None:
        """Refresh trial_info from pre-fetched spans grouped by trial_id.

        Processes:
        1. Trials not yet in cache (missing)
        2. Trials currently marked as "running" (to update scores and detect completion)

        Completed trials are not re-processed as their data is final.

        Args:
            trial_ids: List of all trial IDs to process
            spans_by_trial: Pre-fetched spans grouped by trial_id
        """
        # Determine which trials need processing:
        # 1. Missing trials (not in cache)
        # 2. Running trials (need score updates and completion detection)
        trials_to_process: set[str] = set()

        for tid in trial_ids:
            cached_info = self.cache.get_trial_info(tid)
            if cached_info is None:
                # Not in cache - needs processing
                trials_to_process.add(tid)
            elif cached_info.get("phase") == "running":
                # Running trial - needs re-processing for score updates
                trials_to_process.add(tid)
            # Completed trials (phase in completed/stopped) are skipped

        if not trials_to_process:
            return

        LOGGER.info(
            "BackgroundRefresher: Processing trial_info for %d/%d trials from pre-fetched spans",
            len(trials_to_process),
            len(trial_ids),
        )

        processed = 0
        for trial_id in trials_to_process:
            spans = spans_by_trial.get(trial_id, [])

            # Filter to trial_info relevant spans
            relevant_spans = [
                s for s in spans if s.operation_name in TRIAL_INFO_OPERATION_NAMES
            ]

            if relevant_spans:
                try:
                    trial_info = _extract_trial_info_from_spans(relevant_spans)
                    self.cache.set_trial_info(trial_id, trial_info)
                    processed += 1
                except Exception as e:
                    LOGGER.warning(
                        "BackgroundRefresher: Failed to process trial_info[%s]: %s",
                        trial_id,
                        e,
                    )
            else:
                # No spans found - set unknown phase
                self.cache.set_trial_info(
                    trial_id, {"phase": "unknown", "metadata": {}}
                )

        LOGGER.info(
            "BackgroundRefresher: Processed trial_info for %d trials",
            processed,
        )

    def _refresh_leaderboard_from_spans(
        self,
        spans_by_trial: dict[str, list[SpanData]],
        agent_info_cache: dict[str, Any],
        trial_ids: list[str],
    ) -> None:
        """Refresh leaderboard cache from pre-fetched spans.

        Args:
            spans_by_trial: Pre-fetched spans grouped by trial_id
            agent_info_cache: Pre-populated agent info cache
            trial_ids: List of trial IDs to process
        """
        # Refresh global leaderboard
        leaderboard = _compute_leaderboard_from_spans(
            spans_by_trial, agent_info_cache, trial_ids
        )
        self.cache.set_leaderboard(leaderboard, league=None)

        # Refresh per-league leaderboard
        for league in CACHEABLE_LEAGUES:
            # Get trial IDs for this league from cache
            league_ids = [
                tid for tid in trial_ids if self._trial_matches_league(tid, league)
            ]
            league_leaderboard = _compute_leaderboard_from_spans(
                spans_by_trial, agent_info_cache, league_ids
            )
            self.cache.set_leaderboard(league_leaderboard, league=league)

        LOGGER.info("BackgroundRefresher: Refreshed leaderboard from pre-fetched spans")

    def _refresh_agent_actions_from_spans(
        self,
        spans_by_trial: dict[str, list[SpanData]],
        agent_info_cache: dict[str, Any],
        trial_ids: list[str],
    ) -> None:
        """Refresh agent actions cache from pre-fetched spans.

        Args:
            spans_by_trial: Pre-fetched spans grouped by trial_id
            agent_info_cache: Pre-populated agent info cache
            trial_ids: List of trial IDs to process
        """
        # Refresh global agent actions
        actions = _extract_agent_actions_from_spans(
            spans_by_trial,
            agent_info_cache,
            trial_ids,
            limit=self.config.agent_actions_limit,
            max_trials=self.config.agent_actions_max_trials,
        )
        self.cache.set_agent_actions(actions, league=None)

        # Refresh per-league agent actions
        for league in CACHEABLE_LEAGUES:
            league_ids = [
                tid for tid in trial_ids if self._trial_matches_league(tid, league)
            ]
            league_actions = _extract_agent_actions_from_spans(
                spans_by_trial,
                agent_info_cache,
                league_ids,
                limit=self.config.agent_actions_limit,
                max_trials=self.config.agent_actions_max_trials,
            )
            self.cache.set_agent_actions(league_actions, league=league)

        LOGGER.info(
            "BackgroundRefresher: Refreshed agent actions from pre-fetched spans"
        )

    def _trial_matches_league(self, trial_id: str, league: str) -> bool:
        """Check if a trial matches a specific league.

        Args:
            trial_id: Trial ID to check
            league: League to match (e.g., 'NBA', 'NFL')

        Returns:
            True if trial matches the league
        """
        trial_info = self.cache.get_trial_info(trial_id)
        if trial_info is None:
            return False

        metadata = trial_info.get("metadata", {})
        trial_league = metadata.get("sport_type", "")
        return trial_league.upper() == league.upper()

    async def _refresh_stats(self) -> None:
        """Refresh stats cache (global + per-league)."""
        trial_ids = self.cache.get_trials_list() or []

        # Refresh global stats (pass spans_by_trial for wagered calculation)
        stats = await _compute_stats(
            self.trace_reader, trial_ids, self.cache, self._spans_by_trial
        )
        self.cache.set_stats(stats, league=None)

        # Refresh per-league stats
        for league in CACHEABLE_LEAGUES:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self.cache
            )
            league_stats = await _compute_stats(
                self.trace_reader, filtered_ids, self.cache, self._spans_by_trial
            )
            self.cache.set_stats(league_stats, league=league)

    async def _refresh_games(self) -> None:
        """Refresh games cache (global + per-league)."""
        trial_ids = self.cache.get_trials_list() or []

        # Refresh global games
        games = await _extract_games_from_trials(
            self.trace_reader, trial_ids, self.cache
        )
        self.cache.set_games(games, league=None)

        # Refresh per-league games
        for league in CACHEABLE_LEAGUES:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self.cache
            )
            league_games = await _extract_games_from_trials(
                self.trace_reader, filtered_ids, self.cache
            )
            self.cache.set_games(league_games, league=league)

    def _process_trial_details(
        self,
        trial_id: str,
        spans: list[SpanData],
        existing_items: list[dict[str, Any]],
        existing_max_timestamp: int,
    ) -> None:
        """Process spans for a single trial and update cache.

        Args:
            trial_id: The trial ID
            spans: All spans for this trial (may include old ones)
            existing_items: Previously cached items
            existing_max_timestamp: Max timestamp from previous cache
        """
        if existing_items and existing_max_timestamp > 0:
            # Incremental: filter to only new spans
            new_spans = [s for s in spans if s.start_time > existing_max_timestamp]
            if not new_spans:
                return  # No new data

            new_items = []
            new_max_timestamp = existing_max_timestamp
            is_now_completed = False

            for span in new_spans:
                typed = deserialize_span(span)
                if typed is not None:
                    item = serialize_span_for_ws(typed)
                    new_items.append(item)
                    new_max_timestamp = max(new_max_timestamp, span.start_time)
                    if isinstance(typed, TrialLifecycleSpan) and typed.phase in (
                        "completed",
                        "stopped",
                    ):
                        is_now_completed = True

            if new_items:
                merged_items = existing_items + new_items
                self.cache.set_trial_details(
                    trial_id, merged_items, new_max_timestamp, is_now_completed
                )
        else:
            # Full fetch: process all spans
            items = []
            max_timestamp = 0
            is_completed = False

            for span in spans:
                typed = deserialize_span(span)
                if typed is not None:
                    item = serialize_span_for_ws(typed)
                    items.append(item)
                    max_timestamp = max(max_timestamp, span.start_time)
                    if isinstance(typed, TrialLifecycleSpan) and typed.phase in (
                        "completed",
                        "stopped",
                    ):
                        is_completed = True

            self.cache.set_trial_details(trial_id, items, max_timestamp, is_completed)

    async def _preload_replay_cache(self) -> None:
        """Preload replay data for all completed trials at startup."""
        completed_trial_ids = self.cache.get_completed_trial_ids()
        if not completed_trial_ids:
            LOGGER.info(
                "BackgroundRefresher: No completed trials to preload for replay"
            )
            return

        LOGGER.info(
            "BackgroundRefresher: Preloading replay cache for %d completed trials",
            len(completed_trial_ids),
        )

        # Use semaphore to limit concurrency
        semaphore = asyncio.Semaphore(5)

        async def load_with_semaphore(trial_id: str) -> None:
            async with semaphore:
                await self._load_replay_for_trial(trial_id)

        await asyncio.gather(
            *[load_with_semaphore(tid) for tid in completed_trial_ids],
            return_exceptions=True,
        )

        LOGGER.info(
            "BackgroundRefresher: Replay cache preloaded (%d entries)",
            len(self.replay_cache._cache),
        )

    async def _load_replay_for_trial(self, trial_id: str) -> None:
        """Load replay data for a single completed trial into replay_cache.

        This is called:
        - At startup for all completed trials (preloading)
        - When a live trial transitions to completed
        """
        # Check if already cached
        cached = await self.replay_cache.get(trial_id)
        if cached:
            LOGGER.debug(
                "BackgroundRefresher: Replay for %s already cached, skipping", trial_id
            )
            return

        try:
            # Use the existing _load_replay_data function
            cache_entry, error_reason = await _load_replay_data(
                self.trace_reader,
                self.replay_cache,
                trial_id,
            )

            if cache_entry:
                LOGGER.info(
                    "BackgroundRefresher: Loaded replay for %s (%d items, %d plays)",
                    trial_id,
                    len(cache_entry.items),
                    cache_entry.meta.total_play_count,
                )
            else:
                LOGGER.debug(
                    "BackgroundRefresher: Replay not available for %s: %s",
                    trial_id,
                    error_reason,
                )
        except Exception as e:
            LOGGER.warning(
                "BackgroundRefresher: Failed to load replay for %s: %s",
                trial_id,
                e,
            )

    # -------------------------------------------------------------------------
    # On-demand refresh (for cache miss on new data)
    # -------------------------------------------------------------------------

    async def refresh_trial_info_on_demand(self, trial_id: str) -> dict[str, Any]:
        """Refresh trial_info for a specific trial (on-demand for cache miss)."""
        trial_info = await _extract_trial_info_from_traces(self.trace_reader, trial_id)
        self.cache.set_trial_info(trial_id, trial_info)
        return trial_info

    async def refresh_trial_details_on_demand(
        self, trial_id: str
    ) -> list[dict[str, Any]]:
        """Refresh trial_details for a specific trial (on-demand for cache miss)."""
        # For on-demand single trial, fetch directly
        spans = await self.trace_reader.get_spans(trial_id)
        existing = self.cache.get_trial_details(trial_id)

        if existing is not None:
            existing_items = existing.get("items", [])
            max_ts = existing.get("max_timestamp", 0)
        else:
            existing_items = []
            max_ts = 0

        self._process_trial_details(trial_id, spans, existing_items, max_ts)
        cached = self.cache.get_trial_details(trial_id)
        return cached.get("items", []) if cached else []

    async def refresh_stats_on_demand(self, league: str | None = None) -> StatsResponse:
        """Refresh stats cache on demand (for cache miss)."""
        trial_ids = self.cache.get_trials_list() or []

        if league:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self.cache
            )
            stats = await _compute_stats(self.trace_reader, filtered_ids, self.cache)
        else:
            stats = await _compute_stats(self.trace_reader, trial_ids, self.cache)

        self.cache.set_stats(stats, league=league)
        return stats

    async def refresh_games_on_demand(self, league: str | None = None) -> GamesResponse:
        """Refresh games cache on demand (for cache miss)."""
        trial_ids = self.cache.get_trials_list() or []

        if league:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self.cache
            )
            games = await _extract_games_from_trials(
                self.trace_reader, filtered_ids, self.cache
            )
        else:
            games = await _extract_games_from_trials(
                self.trace_reader, trial_ids, self.cache
            )

        self.cache.set_games(games, league=league)
        return games

    async def refresh_leaderboard_on_demand(
        self, league: str | None = None
    ) -> list[LeaderboardEntry]:
        """Refresh leaderboard cache on demand (for cache miss)."""
        trial_ids = self.cache.get_trials_list() or []

        if league:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self.cache
            )
            leaderboard = await _compute_leaderboard(
                self.trace_reader, filtered_ids, self.cache
            )
        else:
            leaderboard = await _compute_leaderboard(
                self.trace_reader, trial_ids, self.cache
            )

        self.cache.set_leaderboard(leaderboard, league=league)
        return leaderboard

    async def refresh_agent_actions_on_demand(
        self, league: str | None = None
    ) -> list[AgentAction]:
        """Refresh agent actions cache on demand (for cache miss)."""
        trial_ids = self.cache.get_trials_list() or []

        if league:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self.cache
            )
            actions = await _extract_agent_actions(
                self.trace_reader,
                filtered_ids,
                self.cache,
                limit=self.config.agent_actions_limit,
                max_trials=self.config.agent_actions_max_trials,
            )
        else:
            actions = await _extract_agent_actions(
                self.trace_reader,
                trial_ids,
                self.cache,
                limit=self.config.agent_actions_limit,
                max_trials=self.config.agent_actions_max_trials,
            )

        self.cache.set_agent_actions(actions, league=league)
        return actions


# =============================================================================
# Stream and Replay Controllers
# =============================================================================


@dataclass
class StreamController:
    """Per-connection stream state controller for live streams.

    Manages pause/resume state and buffers items during pause for catch-up.
    Items are serialized dicts (not raw SpanData) since the stream endpoint
    works with cached items that are already serialized.
    """

    is_paused: bool = False
    # Buffer for items received during pause (for catch-up mode)
    # Items are serialized dicts from cache, not raw SpanData
    pause_buffer: list[dict[str, Any]] = field(default_factory=list)
    # Max buffer size to prevent memory issues
    max_buffer_size: int = 1000

    def pause(self) -> None:
        self.is_paused = True

    def resume(self) -> None:
        self.is_paused = False

    def buffer_item(self, item: dict[str, Any]) -> None:
        """Buffer an item during pause (for catch-up on resume)."""
        if len(self.pause_buffer) < self.max_buffer_size:
            self.pause_buffer.append(item)

    def drain_buffer(self) -> list[dict[str, Any]]:
        """Get and clear buffered items."""
        items = self.pause_buffer
        self.pause_buffer = []
        return items


@dataclass
class TrialReplayController:
    """Controls replay of a completed trial's historical data.

    Loads from real trace data. Supports 1x, 2x, 4x, 10x, 20x playback speeds.
    Supports seeking to specific play positions using pre-computed metadata.
    """

    trial_id: str
    items: list[dict[str, Any]]
    meta: ReplayMetaInfo
    current_index: int = 0
    speed: float = 1.0  # 1x, 2x, 4x, 10x, 20x
    is_paused: bool = False
    base_interval: float = 2.0  # 2 seconds per event at 1x speed
    heartbeat_interval: float = (
        5.0  # Fixed interval for heartbeat (not affected by speed)
    )
    snapshot_size: int = 20  # Number of items to send in snapshot

    def set_speed(self, speed: float) -> None:
        """Set playback speed (1x, 2x, 4x, 10x, 20x)."""
        allowed = [1.0, 2.0, 4.0, 10.0, 20.0]
        if speed in allowed:
            self.speed = speed
        else:
            # Snap to nearest allowed
            self.speed = min(allowed, key=lambda x: abs(x - speed))
        LOGGER.debug(
            "Replay speed set to %.1fx for trial %s", self.speed, self.trial_id
        )

    def pause(self) -> None:
        self.is_paused = True
        LOGGER.debug(
            "Replay paused at index %d for trial %s", self.current_index, self.trial_id
        )

    def resume(self) -> None:
        self.is_paused = False
        LOGGER.debug(
            "Replay resumed from index %d for trial %s",
            self.current_index,
            self.trial_id,
        )

    def reset(self) -> None:
        self.current_index = 0
        self.is_paused = False

    def get_snapshot_items(self) -> list[dict[str, Any]]:
        """Get initial snapshot items to send on connection."""
        count = min(self.snapshot_size, len(self.items))
        self.current_index = count
        return self.items[:count]

    def get_next_item(self) -> dict[str, Any] | None:
        """Get the next item to send, or None if complete."""
        if self.current_index >= len(self.items):
            return None
        item = self.items[self.current_index]
        self.current_index += 1
        return item

    def get_effective_interval(self) -> float:
        """Get actual playback interval based on speed."""
        return self.base_interval / self.speed

    def is_complete(self) -> bool:
        return self.current_index >= len(self.items)

    def seek_to_play_index(self, play_index: int) -> list[dict[str, Any]]:
        """Seek to a specific play index and return snapshot of items up to that point.

        Args:
            play_index: 0-based index among play items (not all items)

        Returns:
            List of items to send as snapshot (last snapshot_size items up to target)
        """
        if not self.meta.play_item_indices:
            # No plays, return empty
            return []

        # Clamp play_index to valid range
        play_index = max(0, min(play_index, self.meta.total_play_count - 1))

        # Find the actual item index for this play
        target_item_index = self.meta.play_item_indices[play_index]

        # Set current_index to continue from after this item
        self.current_index = target_item_index + 1

        # Return last snapshot_size items up to and including the target
        start = max(0, target_item_index + 1 - self.snapshot_size)
        return self.items[start : target_item_index + 1]

    def get_current_play_index(self) -> int:
        """Get current position in terms of play index (0-based)."""
        # Binary search would be more efficient, but linear is fine for typical sizes
        count = 0
        for play_item_idx in self.meta.play_item_indices:
            if play_item_idx < self.current_index:
                count += 1
            else:
                break
        return count

    def get_status(self) -> WSReplayStatusMessage:
        """Get current replay status."""
        total = len(self.items)
        progress = (self.current_index / total * 100) if total > 0 else 0
        return WSReplayStatusMessage(
            current_index=self.current_index,
            total_items=total,
            current_play_index=self.get_current_play_index(),
            total_play_count=self.meta.total_play_count,
            is_paused=self.is_paused,
            speed=self.speed,
            progress_percent=round(progress, 1),
            timestamp=datetime.now(timezone.utc),
        )


@dataclass
class ArenaServerState:
    """Shared state for the Arena Server."""

    trace_reader: TraceReader
    broadcaster: SpanBroadcaster = field(default_factory=SpanBroadcaster)
    cache: LandingPageCache = field(default_factory=LandingPageCache)
    replay_cache: ReplayCache = field(default_factory=ReplayCache)
    refresher: BackgroundRefresher | None = None  # Set during lifespan
    static_dir: Path | None = None
    poll_interval: float = 1.0  # Seconds between trace polls (for WebSocket)
    trace_backend: str = "jaeger"
    by_alias: bool = False  # Use snake_case keys in REST JSON responses

    # Tracking last poll time per trial for incremental updates (WebSocket)
    _last_poll: dict[str, datetime] = field(default_factory=dict)


_server_state: ArenaServerState | None = None


def get_server_state() -> ArenaServerState:
    """Get the current server state."""
    if _server_state is None:
        raise RuntimeError("Server not initialized")
    return _server_state


def create_arena_app(
    config: "ArenaServerConfig | None" = None,
    *,
    trace_backend: str = "jaeger",
    trace_query_endpoint: str = "http://localhost:16686",
    static_dir: Path | None = None,
    poll_interval: float = 1.0,
    service_name: str = "dojozero",
    by_alias: bool = False,
) -> FastAPI:
    """Create the Arena Server FastAPI application.

    Args:
        config: ArenaServerConfig for cache/query settings. Uses defaults if None.
        trace_backend: Trace backend type ("jaeger" or "sls")
        trace_query_endpoint: Jaeger Query API endpoint
        static_dir: Path to static files (React build output)
        poll_interval: Interval for polling new spans
        service_name: Service name for trace backend
        by_alias: Use serialization aliases (camelCase) in REST JSON responses.

    For SLS backend, configuration comes from environment variables:
        DOJOZERO_SLS_PROJECT: SLS project name
        DOJOZERO_SLS_ENDPOINT: SLS endpoint (e.g., cn-hangzhou.log.aliyuncs.com)
        DOJOZERO_SLS_LOGSTORE: Logstore name (e.g., "dojozero-traces")
    """
    from dojozero.arena_server._config import DEFAULT_CONFIG

    # Use provided config or default for cache/query settings
    resolved_config = config if config is not None else DEFAULT_CONFIG

    # Create trace reader using CLI args
    trace_reader = create_trace_reader(
        backend=trace_backend,
        trace_query_endpoint=trace_query_endpoint,
        service_name=service_name,
    )
    broadcaster = SpanBroadcaster()

    # Pre-compute configs for lifespan closure
    cache_config = CacheConfig.from_arena_config(resolved_config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _server_state

        # Create cache and refresher using config
        cache = LandingPageCache(config=cache_config)
        replay_cache = ReplayCache.from_arena_config(resolved_config)
        refresher = BackgroundRefresher(
            trace_reader=trace_reader,
            cache=cache,
            replay_cache=replay_cache,
            config=cache_config,
        )

        _server_state = ArenaServerState(
            trace_reader=trace_reader,
            broadcaster=broadcaster,
            cache=cache,
            replay_cache=replay_cache,
            refresher=refresher,
            static_dir=static_dir,
            poll_interval=poll_interval,
            trace_backend=trace_backend,
            by_alias=by_alias,
        )

        # Start background refresher and wait for initial cache population
        LOGGER.info("Arena Server starting (waiting for initial cache population)...")
        await refresher.start()
        await refresher.wait_for_ready()

        LOGGER.info(
            "Arena Server ready (trace backend: %s, static_dir: %s, service_name: %s)",
            trace_backend,
            static_dir,
            service_name,
        )
        yield

        # Cleanup
        await refresher.stop()
        close_fn = getattr(trace_reader, "close", None)
        if close_fn is not None:
            await close_fn()
        LOGGER.info("Arena Server shutting down")

    app = FastAPI(
        title="DojoZero Arena Server",
        description="WebSocket streaming and trace queries for arena UI",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------------------
    # Register Endpoints
    # -------------------------------------------------------------------------

    from dojozero.arena_server._endpoints import (
        register_rest_endpoints,
        register_static_file_serving,
        register_websocket_endpoints,
    )

    register_rest_endpoints(app)
    register_websocket_endpoints(app)

    # Register static file serving if configured
    if static_dir:
        register_static_file_serving(app, static_dir)

    return app


async def run_arena_server(
    config: "ArenaServerConfig | None" = None,
    *,
    host: str = "127.0.0.1",
    port: int = 3001,
    trace_backend: str = "jaeger",
    trace_query_endpoint: str = "http://localhost:16686",
    static_dir: Path | None = None,
    service_name: str = "dojozero",
    by_alias: bool = False,
) -> None:
    """Run the Arena Server.

    Args:
        config: ArenaServerConfig for cache/query settings. Uses defaults if None.
        host: Host to bind to
        port: Port to listen on
        trace_backend: Trace backend type ("jaeger" or "sls")
        trace_query_endpoint: Jaeger Query API endpoint (only used when trace_backend="jaeger")
        static_dir: Path to static files (React build output)
        service_name: Service name for Jaeger or SLS trace backend
        by_alias: Use serialization aliases (camelCase) in REST JSON responses.

    For SLS backend, configuration comes from environment variables:
        DOJOZERO_SLS_PROJECT: SLS project name
        DOJOZERO_SLS_ENDPOINT: SLS endpoint (e.g., cn-hangzhou.log.aliyuncs.com)
        DOJOZERO_SLS_LOGSTORE: Logstore name (e.g., "dojozero-traces")
    """
    import uvicorn

    app = create_arena_app(
        config=config,
        trace_backend=trace_backend,
        trace_query_endpoint=trace_query_endpoint,
        static_dir=static_dir,
        service_name=service_name,
        by_alias=by_alias,
    )

    uvicorn_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(uvicorn_config)
    await server.serve()


__all__ = [
    "ArenaServerState",
    "BackgroundRefresher",
    "CategoryFilter",
    "SpanBroadcaster",
    "StreamController",
    "TrialReplayController",
    "WSMessageType",
    "create_arena_app",
    "create_trace_reader",
    "get_server_state",
    "run_arena_server",
]
