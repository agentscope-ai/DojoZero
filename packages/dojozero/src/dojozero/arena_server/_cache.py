import logging
import time
from dataclasses import dataclass, field
from typing import Any
from dojozero.arena_server._models import StatsResponse, GamesResponse

from dojozero.betting import AgentInfo, AgentList
from dojozero.core import deserialize_span, LeaderboardEntry, AgentAction
from dojozero.core._tracing import SpanData

LOGGER = logging.getLogger("dojozero.arena_server.cache")


@dataclass(frozen=True)
class CacheConfig:
    """Configuration for cache refresh and TTLs.

    Background Refresh Model:
    - Single consolidated refresh task fetches ALL data in one pass
    - refresh_interval: How often the background task runs
    - max_ttl: Maximum time to keep data (safety limit)

    Note: This is created from ArenaServerConfig. Use from_arena_config() factory.
    """

    # Background Refresh
    refresh_interval: float = 5.0

    # Cache TTL
    max_cache_ttl: float = 90 * 24 * 3600.0  # 90 days
    completed_trial_ttl: float = 90 * 24 * 3600.0  # 90 days

    # Startup
    startup_timeout: float = 30.0

    # Query Limits
    agent_actions_max_trials: int = 5
    agent_actions_limit: int = 20
    leaderboard_limit: int = 20
    trials_limit: int = 500

    # Time range
    trials_lookback_days: int = 90

    @classmethod
    def from_arena_config(cls, config: "ArenaServerConfig") -> "CacheConfig":
        """Create CacheConfig from ArenaServerConfig."""
        return cls(
            refresh_interval=config.cache.refresh_interval,
            max_cache_ttl=config.cache.max_cache_ttl,
            completed_trial_ttl=config.cache.completed_trial_ttl,
            startup_timeout=config.cache.startup_timeout,
            agent_actions_max_trials=config.query_limits.agent_actions_max_trials,
            agent_actions_limit=config.query_limits.agent_actions_limit,
            leaderboard_limit=config.query_limits.leaderboard_limit,
            trials_limit=config.cache.trials_limit,
            trials_lookback_days=config.cache.trials_lookback_days,
        )


# Import here to avoid circular import
from dojozero.arena_server._config import ArenaServerConfig  # noqa: E402

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

    Note: No lock is needed because BackgroundRefresher is the single writer
    and Python's GIL ensures atomic reference assignments.
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

    # Agent bets index - per-agent bet records (agent_id -> list[BetRecord])
    _agent_bets_index: dict[str, list] = field(default_factory=dict)

    # Agent info cache - single source of truth (agent_id -> AgentInfo)
    _agent_info: dict[str, AgentInfo] = field(default_factory=dict)

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
    # Agent Info Methods - Single source of truth for agent metadata
    # -------------------------------------------------------------------------

    def get_agent_info(self, agent_id: str) -> AgentInfo | None:
        """Get cached agent info by ID."""
        return self._agent_info.get(agent_id)

    def get_all_agent_info(self) -> dict[str, AgentInfo]:
        """Get all cached agent info (for batch operations)."""
        return self._agent_info

    def get_agent_bets_index(self) -> dict[str, list]:
        """Get cached agent bets index (agent_id -> sorted list[BetRecord])."""
        return self._agent_bets_index

    def set_agent_bets_index(self, data: dict[str, list]) -> None:
        """Set agent bets index (overwrites previous)."""
        self._agent_bets_index = data
        LOGGER.debug("Cache SET: agent_bets_index (%d agents)", len(data))

    def get_total_agents(self) -> int:
        """Get total number of cached agents."""
        return len(self._agent_info)

    def update_agent_info_from_spans(self, spans: list[SpanData]) -> int:
        """Extract and merge agent info from spans.

        Looks for agent.agent_initialize spans containing AgentList payloads.
        New agents are added to cache; existing agents are updated with newer info.
        CDN URLs are migrated from old format to new format.

        Args:
            spans: List of spans to extract from

        Returns:
            Number of new agents added (not counting updates)
        """
        added = 0
        for span in spans:
            if span.operation_name != "agent.agent_initialize":
                continue
            typed = deserialize_span(span)
            if isinstance(typed, AgentList):
                for agent_info in typed.agents:
                    if agent_info.agent_id:
                        is_new = agent_info.agent_id not in self._agent_info
                        self._agent_info[agent_info.agent_id] = agent_info
                        if is_new:
                            added += 1
                            LOGGER.debug("Cached new agent: %s", agent_info.agent_id)
                        else:
                            LOGGER.debug("Updated agent: %s", agent_info.agent_id)
        return added

    def clear_agent_info(self) -> None:
        """Clear all cached agent info."""
        self._agent_info.clear()
        LOGGER.debug("Cache CLEAR: agent_info")

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
        # Clear agent info cache
        self._agent_info.clear()
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
                "refresh_interval": self.config.refresh_interval,
                "startup_timeout": self.config.startup_timeout,
                "max_cache_ttl": self.config.max_cache_ttl,
                "completed_trial_ttl": self.config.completed_trial_ttl,
                "trials_lookback_days": self.config.trials_lookback_days,
                "trials_limit": self.config.trials_limit,
                "agent_actions_max_trials": self.config.agent_actions_max_trials,
                "agent_actions_limit": self.config.agent_actions_limit,
                "leaderboard_limit": self.config.leaderboard_limit,
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
                "agent_info_count": len(self._agent_info),
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

    Meta event indices are used to include essential initialization events
    in seek snapshots, ensuring the frontend has complete context.
    """

    total_play_count: int  # Number of items matching core_categories
    play_item_indices: list[int]  # play_index -> item_index mapping
    periods: list[PeriodInfo]  # Period segmentation info

    # Meta event indices for seek snapshots
    agent_initialize_item_index: int | None = None  # Index of agent_initialize event
    game_initialize_item_index: int | None = None  # Index of game_initialize event
    game_start_item_index: int | None = None  # Index of game_start event
    odds_update_indices: list[int] = field(
        default_factory=list
    )  # Indices of odds_update events
    broker_state_update_indices: list[int] = field(
        default_factory=list
    )  # Indices of broker.state_update events
    broker_final_stats_indices: list[int] = field(
        default_factory=list
    )  # Indices of broker.final_stats events


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

    Note: No lock needed - Python dict operations are atomic and this cache
    is tolerant of brief over-capacity during concurrent writes.
    """

    _cache: dict[str, ReplayCacheEntry] = field(default_factory=dict)
    ttl: float = 90 * 24 * 3600.0  # 90 days
    max_entries: int = 100  # Max trials to cache
    core_categories: list[str] = field(default_factory=lambda: ["play", "game_update"])

    @classmethod
    def from_arena_config(cls, config: "ArenaServerConfig") -> "ReplayCache":
        """Create ReplayCache from ArenaServerConfig."""
        return cls(
            ttl=config.replay_cache.ttl,
            max_entries=config.replay_cache.max_entries,
            core_categories=list(config.replay_cache.core_categories),
        )

    def get(self, trial_id: str) -> ReplayCacheEntry | None:
        """Get cached replay entry, or None if not cached/expired."""
        entry = self._cache.get(trial_id)
        if entry and entry.is_valid():
            LOGGER.debug(
                "ReplayCache HIT: %s (%d items, %d plays)",
                trial_id,
                len(entry.items),
                entry.meta.total_play_count,
            )
            return entry
        return None

    def set(
        self, trial_id: str, items: list[dict[str, Any]], meta: ReplayMetaInfo
    ) -> None:
        """Cache replay data for a completed trial."""
        # Evict oldest if at capacity
        if len(self._cache) >= self.max_entries:
            oldest = min(self._cache.items(), key=lambda x: x[1].created_at)
            self._cache.pop(oldest[0], None)
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
        self._cache.pop(trial_id, None)

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
