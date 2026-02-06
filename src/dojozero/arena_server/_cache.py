import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
import logging
from dojozero.arena_server._models import StatsResponse, GamesResponse

from dojozero.betting import AgentInfo, AgentList
from dojozero.core import TraceReader, deserialize_span, LeaderboardEntry, AgentAction

_AGENT_CACHE: dict[str, AgentInfo] = {}
_AGENT_CACHE_LOCK = asyncio.Lock()

LOGGER = logging.getLogger("dojozero.arena_server.cache")


@dataclass(frozen=True)
class CacheConfig:
    """Configuration for cache refresh intervals and TTLs.

    Background Refresh Model:
    - refresh_interval: How often background task refreshes the cache
    - max_ttl: Maximum time to keep data (very long, e.g., 1 month)

    The refresh_interval controls freshness; max_ttl is just a safety limit.
    """

    # -------------------------------------------------------------------------
    # Background Refresh Intervals (how often to refresh each cache type)
    # -------------------------------------------------------------------------

    # List of trial IDs - foundation for other caches
    trials_list_refresh_interval: float = 60.0

    # Aggregated statistics (gamesPlayed, liveNow, wageredToday)
    stats_refresh_interval: float = 30.0

    # Games list (live, upcoming, completed)
    games_refresh_interval: float = 30.0

    # Agent leaderboard - only changes when games complete
    leaderboard_refresh_interval: float = 60.0

    # Live agent actions ticker - needs frequent updates
    agent_actions_refresh_interval: float = 10.0

    # Live trial details (for streaming) - very frequent for live trials
    live_trial_details_refresh_interval: float = 5.0

    # -------------------------------------------------------------------------
    # Maximum Cache TTL (safety limit, not freshness control)
    # -------------------------------------------------------------------------

    # Default max TTL for all caches (1 month)
    max_cache_ttl: float = 30 * 24 * 3600.0  # 30 days

    # TTL for completed/stopped trials (they never change)
    completed_trial_ttl: float = 30 * 24 * 3600.0  # 30 days

    # -------------------------------------------------------------------------
    # Startup Configuration
    # -------------------------------------------------------------------------

    # Max wait time for initial cache population at startup
    startup_timeout: float = 30.0

    # -------------------------------------------------------------------------
    # Query Limits
    # -------------------------------------------------------------------------

    # Max trials to query for agent actions (reduces SLS queries)
    agent_actions_max_trials: int = 5

    # Max actions to return from ticker
    agent_actions_limit: int = 20

    # Days to look back for trials
    trials_lookback_days: int = 7


DEFAULT_CACHE_CONFIG = CacheConfig()
CACHEABLE_LEAGUES: frozenset[str] = frozenset({"NBA", "NFL"})


@dataclass
class CacheEntry:
    """A cache entry with data and expiration time."""

    data: Any
    expires_at: float  # Unix timestamp when this entry expires
    created_at: float = field(default_factory=time.time)

    def is_valid(self) -> bool:
        """Check if the cache entry is still valid (not expired)."""
        return time.time() < self.expires_at

    def age_seconds(self) -> float:
        """Return how old this cache entry is in seconds."""
        return time.time() - self.created_at


@dataclass
class LandingPageCache:
    """Cache for landing page data with background refresh support.

    Background Refresh Architecture:
    - Background tasks proactively refresh all caches at configured intervals
    - User requests ONLY read from cache via get_* methods
    - set_* methods are used by BackgroundRefresher to update caches
    - Each refresh OVERWRITES previous cache (no append, prevents memory bloat)

    Cache Types:
    - trials_list: List of all trial IDs (global)
    - trial_info: Per-trial phase and metadata (per-trial)
    - trial_details: Full span list for live trials (per-trial, dropped on completion)
    - stats: Aggregated statistics (global + per-league)
    - games: Games list (global + per-league)
    - leaderboard: Agent rankings (global + per-league)
    - agent_actions: Recent agent actions (global + per-league)

    Per-league caches are maintained for CACHEABLE_LEAGUES (NBA, NFL).
    """

    # Configuration
    config: CacheConfig = field(default_factory=lambda: DEFAULT_CACHE_CONFIG)

    # Cache storage - global (no filter)
    _trials_list: CacheEntry | None = None
    _trial_info: dict[str, CacheEntry] = field(default_factory=dict)
    _trial_details: dict[str, CacheEntry] = field(default_factory=dict)
    _stats: CacheEntry | None = None
    _leaderboard: CacheEntry | None = None
    _agent_actions: CacheEntry | None = None
    _games: CacheEntry | None = None

    # Cache storage - per-league (for filtered queries)
    # Keys are uppercase league names (e.g., "NBA", "NFL")
    _stats_by_league: dict[str, CacheEntry] = field(default_factory=dict)
    _games_by_league: dict[str, CacheEntry] = field(default_factory=dict)
    _leaderboard_by_league: dict[str, CacheEntry] = field(default_factory=dict)
    _agent_actions_by_league: dict[str, CacheEntry] = field(default_factory=dict)

    # Concurrency control
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # -------------------------------------------------------------------------
    # GET Methods - Read from cache only, return None if not cached
    # -------------------------------------------------------------------------

    def get_trials_list(self) -> list[str] | None:
        """Get cached trials list. Returns None if not cached."""
        if self._trials_list is not None and self._trials_list.is_valid():
            return self._trials_list.data
        return None

    def get_trial_info(self, trial_id: str) -> dict[str, Any] | None:
        """Get cached trial info. Returns None if not cached."""
        entry = self._trial_info.get(trial_id)
        if entry is not None and entry.is_valid():
            return entry.data
        return None

    def get_trial_details(self, trial_id: str) -> dict[str, Any] | None:
        """Get cached trial details. Returns None if not cached.

        Returns dict with keys: items, max_timestamp, is_completed
        """
        entry = self._trial_details.get(trial_id)
        if entry is not None and entry.is_valid():
            return entry.data
        return None

    def get_stats(self, league: str | None = None) -> StatsResponse | None:
        """Get cached stats. Returns None if not cached."""
        if league:
            league_key = league.upper()
            if league_key in CACHEABLE_LEAGUES:
                entry = self._stats_by_league.get(league_key)
                if entry is not None and entry.is_valid():
                    return entry.data
                return None
        # Global stats
        if self._stats is not None and self._stats.is_valid():
            return self._stats.data
        return None

    def get_games(self, league: str | None = None) -> GamesResponse | None:
        """Get cached games. Returns None if not cached."""
        if league:
            league_key = league.upper()
            if league_key in CACHEABLE_LEAGUES:
                entry = self._games_by_league.get(league_key)
                if entry is not None and entry.is_valid():
                    return entry.data
                return None
        # Global games
        if self._games is not None and self._games.is_valid():
            return self._games.data
        return None

    def get_leaderboard(
        self, league: str | None = None
    ) -> list[LeaderboardEntry] | None:
        """Get cached leaderboard. Returns None if not cached."""
        if league:
            league_key = league.upper()
            if league_key in CACHEABLE_LEAGUES:
                entry = self._leaderboard_by_league.get(league_key)
                if entry is not None and entry.is_valid():
                    return entry.data
                return None
        # Global leaderboard
        if self._leaderboard is not None and self._leaderboard.is_valid():
            return self._leaderboard.data
        return None

    def get_agent_actions(self, league: str | None = None) -> list[AgentAction] | None:
        """Get cached agent actions. Returns None if not cached."""
        if league:
            league_key = league.upper()
            if league_key in CACHEABLE_LEAGUES:
                entry = self._agent_actions_by_league.get(league_key)
                if entry is not None and entry.is_valid():
                    return entry.data
                return None
        # Global agent actions
        if self._agent_actions is not None and self._agent_actions.is_valid():
            return self._agent_actions.data
        return None

    def get_live_trial_ids(self) -> list[str]:
        """Get list of live/running trial IDs from cached trial_info."""
        live_trials = []
        for trial_id, entry in self._trial_info.items():
            if entry.is_valid():
                phase = entry.data.get("phase", "")
                if phase == "running":
                    live_trials.append(trial_id)
        return live_trials

    def get_completed_trial_ids(self) -> list[str]:
        """Get list of completed trial IDs from cached trial_info."""
        completed_trials = []
        for trial_id, entry in self._trial_info.items():
            if entry.is_valid():
                phase = entry.data.get("phase", "")
                # Trials that have ended (stopped/terminated/completed)
                if phase in ("stopped", "terminated", "completed"):
                    completed_trials.append(trial_id)
        return completed_trials

    # -------------------------------------------------------------------------
    # SET Methods - Update cache (overwrite previous data)
    # -------------------------------------------------------------------------

    def set_trials_list(self, data: list[str]) -> None:
        """Set trials list cache (overwrites previous)."""
        self._trials_list = CacheEntry(
            data=data,
            expires_at=time.time() + self.config.max_cache_ttl,
        )
        LOGGER.debug("Cache SET: trials_list (%d trials)", len(data))

    def set_trial_info(self, trial_id: str, data: dict[str, Any]) -> None:
        """Set trial info cache (overwrites previous)."""
        phase = data.get("phase", "unknown")
        is_completed = phase in ("completed", "stopped")
        ttl = (
            self.config.completed_trial_ttl
            if is_completed
            else self.config.max_cache_ttl
        )
        self._trial_info[trial_id] = CacheEntry(
            data=data,
            expires_at=time.time() + ttl,
        )
        LOGGER.debug("Cache SET: trial_info[%s] (phase=%s)", trial_id, phase)

    def set_trial_details(
        self,
        trial_id: str,
        items: list[dict[str, Any]],
        max_timestamp: int,
        is_completed: bool,
    ) -> None:
        """Set trial details cache (overwrites previous)."""
        ttl = (
            self.config.completed_trial_ttl
            if is_completed
            else self.config.max_cache_ttl
        )
        self._trial_details[trial_id] = CacheEntry(
            data={
                "items": items,
                "max_timestamp": max_timestamp,
                "is_completed": is_completed,
            },
            expires_at=time.time() + ttl,
        )
        LOGGER.debug(
            "Cache SET: trial_details[%s] (%d items, completed=%s)",
            trial_id,
            len(items),
            is_completed,
        )

    def set_stats(self, data: StatsResponse, league: str | None = None) -> None:
        """Set stats cache (overwrites previous)."""
        entry = CacheEntry(
            data=data,
            expires_at=time.time() + self.config.max_cache_ttl,
        )
        if league:
            league_key = league.upper()
            if league_key in CACHEABLE_LEAGUES:
                self._stats_by_league[league_key] = entry
                LOGGER.debug("Cache SET: stats[%s]", league_key)
                return
        self._stats = entry
        LOGGER.debug("Cache SET: stats (global)")

    def set_games(self, data: GamesResponse, league: str | None = None) -> None:
        """Set games cache (overwrites previous)."""
        entry = CacheEntry(
            data=data,
            expires_at=time.time() + self.config.max_cache_ttl,
        )
        if league:
            league_key = league.upper()
            if league_key in CACHEABLE_LEAGUES:
                self._games_by_league[league_key] = entry
                LOGGER.debug("Cache SET: games[%s]", league_key)
                return
        self._games = entry
        LOGGER.debug("Cache SET: games (global)")

    def set_leaderboard(
        self, data: list[LeaderboardEntry], league: str | None = None
    ) -> None:
        """Set leaderboard cache (overwrites previous)."""
        entry = CacheEntry(
            data=data,
            expires_at=time.time() + self.config.max_cache_ttl,
        )
        if league:
            league_key = league.upper()
            if league_key in CACHEABLE_LEAGUES:
                self._leaderboard_by_league[league_key] = entry
                LOGGER.debug("Cache SET: leaderboard[%s]", league_key)
                return
        self._leaderboard = entry
        LOGGER.debug("Cache SET: leaderboard (global)")

    def set_agent_actions(
        self, data: list[AgentAction], league: str | None = None
    ) -> None:
        """Set agent actions cache (overwrites previous)."""
        entry = CacheEntry(
            data=data,
            expires_at=time.time() + self.config.max_cache_ttl,
        )
        if league:
            league_key = league.upper()
            if league_key in CACHEABLE_LEAGUES:
                self._agent_actions_by_league[league_key] = entry
                LOGGER.debug("Cache SET: agent_actions[%s]", league_key)
                return
        self._agent_actions = entry
        LOGGER.debug("Cache SET: agent_actions (global)")

    # -------------------------------------------------------------------------
    # Cache Management
    # -------------------------------------------------------------------------

    def remove_trial_details(self, trial_id: str) -> None:
        """Remove trial details from cache (called when trial completes)."""
        if trial_id in self._trial_details:
            del self._trial_details[trial_id]
            LOGGER.debug("Cache REMOVE: trial_details[%s]", trial_id)

    def invalidate_trial(self, trial_id: str) -> None:
        """Invalidate cache for a specific trial."""
        if trial_id in self._trial_info:
            del self._trial_info[trial_id]
        if trial_id in self._trial_details:
            del self._trial_details[trial_id]
        LOGGER.debug("Invalidated cache for trial: %s", trial_id)

    def invalidate_all(self) -> None:
        """Invalidate all cached data."""
        self._trials_list = None
        self._trial_info.clear()
        self._trial_details.clear()
        self._stats = None
        self._leaderboard = None
        self._agent_actions = None
        self._games = None
        # Clear per-league caches
        self._stats_by_league.clear()
        self._games_by_league.clear()
        self._leaderboard_by_league.clear()
        self._agent_actions_by_league.clear()
        LOGGER.debug("Invalidated all cache entries")

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics for debugging/monitoring."""

        def _entry_info(entry: CacheEntry | None) -> dict[str, Any]:
            if entry is None:
                return {"status": "empty"}
            return {
                "status": "valid" if entry.is_valid() else "expired",
                "age_seconds": round(entry.age_seconds(), 1),
                "expires_in": round(entry.expires_at - time.time(), 1),
            }

        def _league_cache_info(cache: dict[str, CacheEntry]) -> dict[str, Any]:
            return {league: _entry_info(entry) for league, entry in cache.items()}

        return {
            "config": {
                "trials_list_refresh_interval": self.config.trials_list_refresh_interval,
                "stats_refresh_interval": self.config.stats_refresh_interval,
                "games_refresh_interval": self.config.games_refresh_interval,
                "leaderboard_refresh_interval": self.config.leaderboard_refresh_interval,
                "agent_actions_refresh_interval": self.config.agent_actions_refresh_interval,
                "max_cache_ttl": self.config.max_cache_ttl,
                "completed_trial_ttl": self.config.completed_trial_ttl,
                "cacheable_leagues": list(CACHEABLE_LEAGUES),
            },
            "caches": {
                "trials_list": _entry_info(self._trials_list),
                "stats": _entry_info(self._stats),
                "games": _entry_info(self._games),
                "leaderboard": _entry_info(self._leaderboard),
                "agent_actions": _entry_info(self._agent_actions),
                "trial_info_count": len(self._trial_info),
                "trial_details_count": len(self._trial_details),
            },
            "caches_by_league": {
                "stats": _league_cache_info(self._stats_by_league),
                "games": _league_cache_info(self._games_by_league),
                "leaderboard": _league_cache_info(self._leaderboard_by_league),
                "agent_actions": _league_cache_info(self._agent_actions_by_league),
            },
        }


@dataclass
class PeriodInfo:
    """Information about a single period/quarter in a game."""

    period: int
    play_count: int  # Number of plays in this period
    start_play_index: int  # Index of first play in this period (0-based)


@dataclass
class ReplayMetaInfo:
    """Pre-computed metadata for replay progress tracking.

    Computed once when caching replay data. Enables O(1) seek operations
    and provides period segmentation for frontend progress bar.
    """

    total_play_count: int  # Number of items matching core_categories
    play_item_indices: list[int]  # play_index -> item_index mapping
    periods: list[PeriodInfo]  # Period segmentation info


@dataclass
class ReplayCacheEntry:
    """Cache entry for replay data."""

    items: list[dict[str, Any]]  # Serialized spans for WS
    meta: ReplayMetaInfo  # Pre-computed metadata
    created_at: float = field(default_factory=time.time)
    ttl: float = 3600.0  # 1 hour default

    def is_valid(self) -> bool:
        return time.time() < (self.created_at + self.ttl)


@dataclass
class ReplayCache:
    """Cache for completed trial replay data.

    Only stores data for trials that have ended (trial.stopped/terminated).
    Reduces SLS queries for frequently replayed trials.

    The cache stores both the serialized items and pre-computed metadata
    (play indices, period info) to enable efficient seek operations.

    Replay data is preloaded at startup and refreshed when:
    - A new trial completion is detected
    - User requests trigger on-demand loading
    TTL is set to 7 days to match trials_lookback_days.
    """

    _cache: dict[str, ReplayCacheEntry] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ttl: float = 7 * 24 * 3600.0  # 7 days (matches trials_lookback_days)
    max_entries: int = 100  # Max trials to cache
    core_categories: list[str] = field(default_factory=lambda: ["play", "game_update"])

    async def get(self, trial_id: str) -> ReplayCacheEntry | None:
        """Get cached replay entry, or None if not cached/expired."""
        async with self._lock:
            entry = self._cache.get(trial_id)
            if entry and entry.is_valid():
                LOGGER.debug(
                    "ReplayCache HIT: %s (%d items, %d plays)",
                    trial_id,
                    len(entry.items),
                    entry.meta.total_play_count,
                )
                return entry
            elif entry:
                # Expired, remove it
                del self._cache[trial_id]
                LOGGER.debug("ReplayCache EXPIRED: %s", trial_id)
            return None

    async def set(
        self, trial_id: str, items: list[dict[str, Any]], meta: ReplayMetaInfo
    ) -> None:
        """Cache replay data for a completed trial."""
        async with self._lock:
            # Evict oldest if at capacity
            if len(self._cache) >= self.max_entries:
                oldest = min(self._cache.items(), key=lambda x: x[1].created_at)
                del self._cache[oldest[0]]
                LOGGER.debug("ReplayCache evicted: %s", oldest[0])

            self._cache[trial_id] = ReplayCacheEntry(
                items=items,
                meta=meta,
                ttl=self.ttl,
            )
            LOGGER.info(
                "ReplayCache SET: %s (%d items, %d plays, %d periods)",
                trial_id,
                len(items),
                meta.total_play_count,
                len(meta.periods),
            )

    def invalidate(self, trial_id: str) -> None:
        """Remove a trial from cache."""
        if trial_id in self._cache:
            del self._cache[trial_id]

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        valid_count = sum(1 for e in self._cache.values() if e.is_valid())
        return {
            "total_entries": len(self._cache),
            "valid_entries": valid_count,
            "max_entries": self.max_entries,
            "ttl": self.ttl,
            "core_categories": self.core_categories,
        }


# ============================================================================
# Global Agent Cache
# ============================================================================

# Global agent cache: agent_id → AgentInfo
# Populated lazily from agent.agent_initialize spans


async def _populate_agent_cache(
    trace_reader: TraceReader,
    trial_id: str,
) -> None:
    """Populate agent cache from agent.agent_initialize spans.

    This function is called lazily when an agent_id is not found in cache.
    It queries the trace store for agent.agent_initialize spans and populates
    the cache with AgentInfo objects.
    """
    try:
        spans = await trace_reader.get_spans(
            trial_id,
            operation_names=["agent.agent_initialize"],
        )
    except Exception as e:
        LOGGER.warning(
            "Failed to get agent.agent_initialize spans for trial '%s': %s",
            trial_id,
            e,
        )
        return

    async with _AGENT_CACHE_LOCK:
        for span in spans:
            typed = deserialize_span(span)
            if isinstance(typed, AgentList):
                for agent_info in typed.agents:
                    if agent_info.agent_id:
                        _AGENT_CACHE[agent_info.agent_id] = agent_info
                        LOGGER.debug(
                            "Cached agent: %s (from trial %s)",
                            agent_info.agent_id,
                            trial_id,
                        )


async def get_cached_agent(
    trace_reader: TraceReader,
    agent_id: str,
    trial_id: str,
) -> AgentInfo | None:
    """Get agent info from cache, populating if needed.

    Uses lazy loading: if agent_id is not in cache, queries trace store
    for agent.agent_initialize spans from the given trial.

    Args:
        trace_reader: TraceReader to query for agent info
        agent_id: The agent ID to look up
        trial_id: The trial ID to query if cache miss

    Returns:
        AgentInfo if found, None otherwise
    """
    # Check cache first
    if agent_id in _AGENT_CACHE:
        return _AGENT_CACHE[agent_id]

    # Cache miss: try to populate from trace store
    await _populate_agent_cache(trace_reader, trial_id)

    # Check again after population
    return _AGENT_CACHE.get(agent_id)
