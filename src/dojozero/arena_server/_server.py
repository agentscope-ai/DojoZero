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

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dojozero.arena_server._models import (
    AgentActionsResponse,
    BetSummary,
    GameCardData,
    GamesResponse,
    LandingResponse,
    LeaderboardResponse,
    ReplayResponse,
    StatsResponse,
    TrialDetailResponse,
    TrialListItem,
    WSHeartbeatMessage,
    WSReplayMetaInfoMessage,
    WSReplayStatusMessage,
    WSReplayUnavailableMessage,
    WSSnapshotMessage,
    WSSpanMessage,
    WSStreamStatusMessage,
    WSTrialEndedMessage,
)
from dojozero.betting import AgentInfo, AgentList, AgentResponseMessage
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
from dojozero.data._models import BaseGameUpdateEvent, GameInitializeEvent, TeamIdentity

# Rebuild Pydantic models to resolve forward references
# This must happen after imports to avoid circular import issues
AgentAction.model_rebuild()
LeaderboardEntry.model_rebuild()
BetSummary.model_rebuild()

# Type alias for replay error reasons
ReplayErrorReason = Literal["trial_not_found", "trial_still_running", "no_data"]


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


# NBA team data lookup: tricode -> TeamIdentity
# Used to fill in team details when not available in trial metadata
# Logo URLs use ESPN CDN: https://a.espncdn.com/i/teamlogos/nba/500/{tricode}.png
_NBA_TEAMS: dict[str, TeamIdentity] = {
    "ATL": TeamIdentity(
        name="Hawks",
        tricode="ATL",
        location="Atlanta",
        color="#E03A3E",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/atl.png",
    ),
    "BOS": TeamIdentity(
        name="Celtics",
        tricode="BOS",
        location="Boston",
        color="#007A33",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/bos.png",
    ),
    "BKN": TeamIdentity(
        name="Nets",
        tricode="BKN",
        location="Brooklyn",
        color="#000000",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/bkn.png",
    ),
    "CHA": TeamIdentity(
        name="Hornets",
        tricode="CHA",
        location="Charlotte",
        color="#1D1160",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/cha.png",
    ),
    "CHI": TeamIdentity(
        name="Bulls",
        tricode="CHI",
        location="Chicago",
        color="#CE1141",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/chi.png",
    ),
    "CLE": TeamIdentity(
        name="Cavaliers",
        tricode="CLE",
        location="Cleveland",
        color="#860038",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/cle.png",
    ),
    "DAL": TeamIdentity(
        name="Mavericks",
        tricode="DAL",
        location="Dallas",
        color="#00538C",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/dal.png",
    ),
    "DEN": TeamIdentity(
        name="Nuggets",
        tricode="DEN",
        location="Denver",
        color="#0E2240",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/den.png",
    ),
    "DET": TeamIdentity(
        name="Pistons",
        tricode="DET",
        location="Detroit",
        color="#C8102E",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/det.png",
    ),
    "GSW": TeamIdentity(
        name="Warriors",
        tricode="GSW",
        location="Golden State",
        color="#1D428A",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/gs.png",
    ),
    "HOU": TeamIdentity(
        name="Rockets",
        tricode="HOU",
        location="Houston",
        color="#CE1141",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/hou.png",
    ),
    "IND": TeamIdentity(
        name="Pacers",
        tricode="IND",
        location="Indiana",
        color="#002D62",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/ind.png",
    ),
    "LAC": TeamIdentity(
        name="Clippers",
        tricode="LAC",
        location="Los Angeles",
        color="#C8102E",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/lac.png",
    ),
    "LAL": TeamIdentity(
        name="Lakers",
        tricode="LAL",
        location="Los Angeles",
        color="#552583",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/lal.png",
    ),
    "MEM": TeamIdentity(
        name="Grizzlies",
        tricode="MEM",
        location="Memphis",
        color="#5D76A9",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/mem.png",
    ),
    "MIA": TeamIdentity(
        name="Heat",
        tricode="MIA",
        location="Miami",
        color="#98002E",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/mia.png",
    ),
    "MIL": TeamIdentity(
        name="Bucks",
        tricode="MIL",
        location="Milwaukee",
        color="#00471B",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/mil.png",
    ),
    "MIN": TeamIdentity(
        name="Timberwolves",
        tricode="MIN",
        location="Minnesota",
        color="#0C2340",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/min.png",
    ),
    "NOP": TeamIdentity(
        name="Pelicans",
        tricode="NOP",
        location="New Orleans",
        color="#0C2340",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/no.png",
    ),
    "NYK": TeamIdentity(
        name="Knicks",
        tricode="NYK",
        location="New York",
        color="#F58426",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/ny.png",
    ),
    "OKC": TeamIdentity(
        name="Thunder",
        tricode="OKC",
        location="Oklahoma City",
        color="#007AC1",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/okc.png",
    ),
    "ORL": TeamIdentity(
        name="Magic",
        tricode="ORL",
        location="Orlando",
        color="#0077C0",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/orl.png",
    ),
    "PHI": TeamIdentity(
        name="76ers",
        tricode="PHI",
        location="Philadelphia",
        color="#006BB6",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/phi.png",
    ),
    "PHX": TeamIdentity(
        name="Suns",
        tricode="PHX",
        location="Phoenix",
        color="#1D1160",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/phx.png",
    ),
    "POR": TeamIdentity(
        name="Trail Blazers",
        tricode="POR",
        location="Portland",
        color="#E03A3E",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/por.png",
    ),
    "SAC": TeamIdentity(
        name="Kings",
        tricode="SAC",
        location="Sacramento",
        color="#5A2D81",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/sac.png",
    ),
    "SAS": TeamIdentity(
        name="Spurs",
        tricode="SAS",
        location="San Antonio",
        color="#C4CED4",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/sa.png",
    ),
    "TOR": TeamIdentity(
        name="Raptors",
        tricode="TOR",
        location="Toronto",
        color="#CE1141",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/tor.png",
    ),
    "UTA": TeamIdentity(
        name="Jazz",
        tricode="UTA",
        location="Utah",
        color="#002B5C",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/utah.png",
    ),
    "WAS": TeamIdentity(
        name="Wizards",
        tricode="WAS",
        location="Washington",
        color="#002B5C",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/wsh.png",
    ),
}

# NFL team data lookup
# Logo URLs use ESPN CDN: https://a.espncdn.com/i/teamlogos/nfl/500/{tricode}.png
_NFL_TEAMS: dict[str, TeamIdentity] = {
    "KC": TeamIdentity(
        name="Chiefs",
        tricode="KC",
        location="Kansas City",
        color="#E31837",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/kc.png",
    ),
    "SF": TeamIdentity(
        name="49ers",
        tricode="SF",
        location="San Francisco",
        color="#AA0000",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/sf.png",
    ),
    "BUF": TeamIdentity(
        name="Bills",
        tricode="BUF",
        location="Buffalo",
        color="#00338D",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/buf.png",
    ),
    "PHI": TeamIdentity(
        name="Eagles",
        tricode="PHI",
        location="Philadelphia",
        color="#004C54",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/phi.png",
    ),
    "DAL": TeamIdentity(
        name="Cowboys",
        tricode="DAL",
        location="Dallas",
        color="#003594",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/dal.png",
    ),
    "GB": TeamIdentity(
        name="Packers",
        tricode="GB",
        location="Green Bay",
        color="#203731",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/gb.png",
    ),
}

_DEFAULT_TEAM_COLOR = "#666666"


def _get_team_identity(tricode: str, league: str = "NBA") -> TeamIdentity:
    """Get team identity by tricode.

    Returns TeamIdentity from static lookup. Falls back to a minimal identity
    with the tricode as the name if not found, but still generates a logo URL
    using the ESPN CDN pattern.
    """
    teams = _NBA_TEAMS if league == "NBA" else _NFL_TEAMS
    if tricode in teams:
        return teams[tricode]

    # Generate logo URL dynamically for teams not in static lookup
    league_lower = league.lower()
    logo_url = (
        f"https://a.espncdn.com/i/teamlogos/{league_lower}/500/{tricode.lower()}.png"
    )

    return TeamIdentity(
        name=tricode,
        tricode=tricode,
        color=_DEFAULT_TEAM_COLOR,
        logo_url=logo_url,
    )


# ============================================================================
# Global Agent Cache
# ============================================================================

# Global agent cache: agent_id → AgentInfo
# Populated lazily from agent.agent_initialize spans
_AGENT_CACHE: dict[str, AgentInfo] = {}
_AGENT_CACHE_LOCK = asyncio.Lock()


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
# Cache Configuration
# =============================================================================
#
# Background Refresh Architecture:
# - Background tasks proactively refresh all caches at configured intervals
# - User requests ONLY read from cache (never trigger fetches directly)
# - On cache miss (new data not yet in cache), trigger refresh and wait
# - Server blocks on initial cache population at startup
#
# This eliminates:
# - Cache stampede (concurrent requests hitting SLS on TTL expiry)
# - User-visible latency (first request after expiration)
# - Cold start penalty (initial cache population happens at startup)
#


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


# Default configuration instance
DEFAULT_CACHE_CONFIG = CacheConfig()

# Leagues that should be cached separately when filtered
# Add new leagues here as they become supported
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

    def get_leaderboard(self, league: str | None = None) -> list[LeaderboardEntry] | None:
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
        ttl = self.config.completed_trial_ttl if is_completed else self.config.max_cache_ttl
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
        ttl = self.config.completed_trial_ttl if is_completed else self.config.max_cache_ttl
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


# =============================================================================
# Replay Cache
# =============================================================================


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


# =============================================================================
# Background Cache Refresher
# =============================================================================


@dataclass
class BackgroundRefresher:
    """Background cache refresh manager.

    Proactively refreshes all caches at configured intervals. User requests
    only read from cache and never trigger fetches directly (except for cache
    miss on truly new data).

    Refresh Strategy:
    - trials_list: Refreshed at trials_list_refresh_interval
    - stats/games: Refreshed at their respective intervals (global + per-league)
    - leaderboard: Refreshed at leaderboard_refresh_interval (global + per-league)
    - agent_actions: Refreshed at agent_actions_refresh_interval (global + per-league)
    - trial_info: Refreshed for live trials only
    - trial_details: Refreshed for live trials only; removed when trial completes
    - replay_cache: Preloaded at startup; refreshed when new trial completion detected
      or on user demand (NOT periodically)

    Startup:
    - Performs initial refresh of all caches
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

        # Start periodic refresh tasks
        self._tasks.append(
            asyncio.create_task(
                self._refresh_loop(
                    "trials_list",
                    self._refresh_trials_list,
                    self.config.trials_list_refresh_interval,
                )
            )
        )
        self._tasks.append(
            asyncio.create_task(
                self._refresh_loop(
                    "stats",
                    self._refresh_stats,
                    self.config.stats_refresh_interval,
                )
            )
        )
        self._tasks.append(
            asyncio.create_task(
                self._refresh_loop(
                    "games",
                    self._refresh_games,
                    self.config.games_refresh_interval,
                )
            )
        )
        self._tasks.append(
            asyncio.create_task(
                self._refresh_loop(
                    "leaderboard",
                    self._refresh_leaderboard,
                    self.config.leaderboard_refresh_interval,
                )
            )
        )
        self._tasks.append(
            asyncio.create_task(
                self._refresh_loop(
                    "agent_actions",
                    self._refresh_agent_actions,
                    self.config.agent_actions_refresh_interval,
                )
            )
        )
        self._tasks.append(
            asyncio.create_task(
                self._refresh_loop(
                    "live_trials",
                    self._refresh_live_trials,
                    self.config.live_trial_details_refresh_interval,
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

    async def _refresh_all(self) -> None:
        """Refresh all caches (used for initial population)."""
        LOGGER.info("BackgroundRefresher: Refreshing all caches...")

        # 1. Refresh trials list first (foundation for other caches)
        await self._refresh_trials_list()

        # 2. Refresh aggregated caches in parallel
        await asyncio.gather(
            self._refresh_stats(),
            self._refresh_games(),
            self._refresh_leaderboard(),
            self._refresh_agent_actions(),
            return_exceptions=True,
        )

        # 3. Refresh live trial details
        await self._refresh_live_trials()

        # 4. Preload replay data for completed trials
        await self._preload_replay_cache()

        LOGGER.info("BackgroundRefresher: All caches refreshed")

    async def _refresh_trials_list(self) -> None:
        """Refresh trials list cache."""
        start_dt = datetime.now(timezone.utc) - timedelta(
            days=self.config.trials_lookback_days
        )
        trial_ids = await self.trace_reader.list_trials(start_time=start_dt, limit=500)

        if trial_ids:
            self.cache.set_trials_list(trial_ids)

            # Also refresh trial_info for all trials
            await self._refresh_trial_info_batch(trial_ids)

    async def _refresh_trial_info_batch(self, trial_ids: list[str]) -> None:
        """Refresh trial_info for a batch of trials."""
        for trial_id in trial_ids:
            try:
                trial_info = await _extract_trial_info_from_traces(
                    self.trace_reader, trial_id
                )
                self.cache.set_trial_info(trial_id, trial_info)
            except Exception as e:
                LOGGER.warning(
                    "BackgroundRefresher: Failed to refresh trial_info[%s]: %s",
                    trial_id,
                    e,
                )

    async def _refresh_stats(self) -> None:
        """Refresh stats cache (global + per-league)."""
        trial_ids = self.cache.get_trials_list() or []

        # Refresh global stats
        stats = await _compute_stats(self.trace_reader, trial_ids, self.cache)
        self.cache.set_stats(stats, league=None)

        # Refresh per-league stats
        for league in CACHEABLE_LEAGUES:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self.cache
            )
            league_stats = await _compute_stats(
                self.trace_reader, filtered_ids, self.cache
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

    async def _refresh_leaderboard(self) -> None:
        """Refresh leaderboard cache (global + per-league)."""
        trial_ids = self.cache.get_trials_list() or []

        # Refresh global leaderboard
        leaderboard = await _compute_leaderboard(self.trace_reader, trial_ids)
        self.cache.set_leaderboard(leaderboard, league=None)

        # Refresh per-league leaderboard
        for league in CACHEABLE_LEAGUES:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self.cache
            )
            league_leaderboard = await _compute_leaderboard(
                self.trace_reader, filtered_ids
            )
            self.cache.set_leaderboard(league_leaderboard, league=league)

    async def _refresh_agent_actions(self) -> None:
        """Refresh agent actions cache (global + per-league)."""
        trial_ids = self.cache.get_trials_list() or []

        # Refresh global agent actions
        actions = await _extract_agent_actions(
            self.trace_reader,
            trial_ids,
            limit=self.config.agent_actions_limit,
            max_trials=self.config.agent_actions_max_trials,
        )
        self.cache.set_agent_actions(actions, league=None)

        # Refresh per-league agent actions
        for league in CACHEABLE_LEAGUES:
            filtered_ids = await _filter_trials_by_league(
                self.trace_reader, trial_ids, league, self.cache
            )
            league_actions = await _extract_agent_actions(
                self.trace_reader,
                filtered_ids,
                limit=self.config.agent_actions_limit,
                max_trials=self.config.agent_actions_max_trials,
            )
            self.cache.set_agent_actions(league_actions, league=league)

    async def _refresh_live_trials(self) -> None:
        """Refresh trial_details for live trials only.

        Also handles trial completion: when a trial transitions from live to
        completed, we remove its trial_details and load replay data.
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

        # Refresh trial_details for live trials
        for trial_id in current_live:
            try:
                await self._refresh_trial_details(trial_id)
            except Exception as e:
                LOGGER.warning(
                    "BackgroundRefresher: Failed to refresh trial_details[%s]: %s",
                    trial_id,
                    e,
                )

    async def _refresh_trial_details(self, trial_id: str) -> None:
        """Refresh trial_details for a single trial (incremental fetch)."""
        existing = self.cache.get_trial_details(trial_id)

        if existing is not None:
            # Incremental fetch: only get spans since last fetch
            items = existing.get("items", [])
            max_timestamp = existing.get("max_timestamp", 0)
            is_completed = existing.get("is_completed", False)

            if is_completed:
                # Already completed, no need to refresh
                return

            start_time = datetime.fromtimestamp(
                max_timestamp / 1_000_000, tz=timezone.utc
            )
            new_spans = await self.trace_reader.get_spans(trial_id, start_time=start_time)

            # Serialize and merge
            new_items = []
            new_max_timestamp = max_timestamp
            is_now_completed = False

            for span in new_spans:
                typed = deserialize_span(span)
                if typed is not None:
                    new_items.append(serialize_span_for_ws(typed))
                    new_max_timestamp = max(new_max_timestamp, span.start_time)
                    if isinstance(typed, TrialLifecycleSpan) and typed.phase in (
                        "completed",
                        "stopped",
                    ):
                        is_now_completed = True

            if new_items:
                merged_items = items + new_items
                self.cache.set_trial_details(
                    trial_id, merged_items, new_max_timestamp, is_now_completed
                )
        else:
            # Full fetch
            spans = await self.trace_reader.get_spans(trial_id)

            items = []
            max_timestamp = 0
            is_completed = False

            for span in spans:
                typed = deserialize_span(span)
                if typed is not None:
                    items.append(serialize_span_for_ws(typed))
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
            LOGGER.info("BackgroundRefresher: No completed trials to preload for replay")
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
        await self._refresh_trial_details(trial_id)
        cached = self.cache.get_trial_details(trial_id)
        return cached.get("items", []) if cached else []

    async def refresh_stats_on_demand(
        self, league: str | None = None
    ) -> StatsResponse:
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

    async def refresh_games_on_demand(
        self, league: str | None = None
    ) -> GamesResponse:
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
            leaderboard = await _compute_leaderboard(self.trace_reader, filtered_ids)
        else:
            leaderboard = await _compute_leaderboard(self.trace_reader, trial_ids)

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
                limit=self.config.agent_actions_limit,
                max_trials=self.config.agent_actions_max_trials,
            )
        else:
            actions = await _extract_agent_actions(
                self.trace_reader,
                trial_ids,
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

    Manages pause/resume state and buffers spans during pause for catch-up.
    """

    is_paused: bool = False
    # Buffer for spans received during pause (for catch-up mode)
    pause_buffer: list[SpanData] = field(default_factory=list)
    # Max buffer size to prevent memory issues
    max_buffer_size: int = 1000

    def pause(self) -> None:
        self.is_paused = True

    def resume(self) -> None:
        self.is_paused = False

    def buffer_span(self, span: SpanData) -> None:
        """Buffer a span during pause (for catch-up on resume)."""
        if len(self.pause_buffer) < self.max_buffer_size:
            self.pause_buffer.append(span)

    def drain_buffer(self) -> list[SpanData]:
        """Get and clear buffered spans."""
        spans = self.pause_buffer
        self.pause_buffer = []
        return spans


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
    by_alias: bool = True  # Use camelCase aliases in REST JSON responses

    # Tracking last poll time per trial for incremental updates (WebSocket)
    _last_poll: dict[str, datetime] = field(default_factory=dict)


_server_state: ArenaServerState | None = None


def get_server_state() -> ArenaServerState:
    """Get the current server state."""
    if _server_state is None:
        raise RuntimeError("Server not initialized")
    return _server_state


async def _extract_trial_info_from_traces(
    trace_reader: TraceReader,
    trial_id: str,
) -> dict[str, Any]:
    """Extract trial phase and metadata from trace spans.

    Uses filtered queries to only fetch relevant spans (trial lifecycle,
    game_initialize, game_result, game_update) instead of all spans.

    Returns:
        dict with "phase", "metadata", and optional "game_init" extracted from spans
    """
    try:
        # Only fetch spans needed for trial info extraction
        spans = await trace_reader.get_spans(
            trial_id,
            operation_names=[
                "trial.started",
                "trial.stopped",
                "trial.terminated",
                "event.game_initialize",
                "event.game_result",
                "event.nba_game_update",
                "event.nfl_game_update",
            ],
        )
    except Exception as e:
        LOGGER.warning("Failed to get spans for trial '%s': %s", trial_id, e)
        return {"phase": "unknown", "metadata": {}}

    has_started = False
    has_stopped = False
    has_game_result = False  # Indicates game has completed
    latest_start_time = 0
    latest_stop_time = 0

    # Metadata to extract from spans
    metadata: dict[str, Any] = {}
    game_init: GameInitializeEvent | None = None
    latest_game_update: BaseGameUpdateEvent | None = None
    latest_game_update_time = 0

    for span in spans:
        typed = deserialize_span(span)

        if isinstance(typed, TrialLifecycleSpan):
            if typed.phase == "started":
                has_started = True
                if typed.start_time > latest_start_time:
                    latest_start_time = typed.start_time
                # Build metadata from typed fields
                metadata.update(
                    {
                        "home_team_tricode": typed.home_team_tricode,
                        "away_team_tricode": typed.away_team_tricode,
                        "home_team_name": typed.home_team_name,
                        "away_team_name": typed.away_team_name,
                        "game_date": typed.game_date,
                        "sport_type": typed.sport_type,
                        "espn_game_id": typed.espn_game_id,
                        **typed.extra_metadata,
                    }
                )
            elif typed.phase in ("stopped", "terminated"):
                has_stopped = True
                if typed.start_time > latest_stop_time:
                    latest_stop_time = typed.start_time

        elif isinstance(typed, GameInitializeEvent):
            game_init = typed

        # Track latest game update event for live scores
        elif isinstance(typed, BaseGameUpdateEvent):
            # Use span timestamp to find the latest update
            span_time = span.start_time
            if span_time > latest_game_update_time:
                latest_game_update_time = span_time
                latest_game_update = typed

        # Check for game completion spans (NBA/NFL game results)
        elif "game_result" in span.operation_name:
            has_game_result = True

    # Add live scores to metadata if we have a game update
    if latest_game_update is not None:
        metadata["home_score"] = latest_game_update.home_score
        metadata["away_score"] = latest_game_update.away_score
        metadata["period"] = latest_game_update.period
        metadata["game_clock"] = latest_game_update.game_clock

    # Determine phase
    if has_stopped and latest_stop_time >= latest_start_time:
        phase = "stopped"
    elif has_game_result:
        # Game has concluded (game_result span found)
        phase = "completed"
    elif has_started and not has_stopped:
        phase = "running"
    elif has_stopped:
        phase = "stopped"
    elif spans:
        phase = "running"
    else:
        phase = "unknown"

    return {"phase": phase, "metadata": metadata, "game_init": game_init}


async def _filter_trials_by_league(
    trace_reader: TraceReader,
    trial_ids: list[str],
    league: str | None,
    cache: "LandingPageCache | None" = None,
) -> list[str]:
    """Filter trial IDs by league/sport type.

    This is a pure filtering function that takes an existing list of trial IDs
    and returns only those matching the specified league.

    Args:
        trace_reader: TraceReader for fetching trial info
        trial_ids: List of trial IDs to filter
        league: League to filter by (e.g., 'NBA', 'NFL'). None means return all.
        cache: Optional cache for trial info (uses cache if available, fetches if not)

    Returns:
        Filtered list of trial IDs matching the specified league
    """
    if league is None:
        return trial_ids

    league_upper = league.upper()
    filtered: list[str] = []

    for trial_id in trial_ids:
        try:
            # Try cache first, then fetch if not cached
            trial_info = None
            if cache is not None:
                trial_info = cache.get_trial_info(trial_id)

            if trial_info is None:
                trial_info = await _extract_trial_info_from_traces(
                    trace_reader, trial_id
                )
                # Store in cache if available
                if cache is not None:
                    cache.set_trial_info(trial_id, trial_info)

            metadata = trial_info.get("metadata", {})
            # Note: BettingTrialMetadata has no `league` field.
            # `sport_type` is used instead and is treated as the league.
            trial_league = metadata.get("sport_type", "")

            if trial_league.upper() == league_upper:
                filtered.append(trial_id)

        except Exception as e:
            LOGGER.warning(
                "Failed to get info for trial '%s' during filtering: %s",
                trial_id,
                e,
            )
            continue

    LOGGER.debug(
        "Filtered trials by league '%s': %d/%d matched",
        league,
        len(filtered),
        len(trial_ids),
    )
    return filtered


def _resolve_team_identity(
    team: TeamIdentity | str,
    fallback_tricode: str,
    fallback_name: str,
    league: str,
) -> TeamIdentity:
    """Resolve a team to a TeamIdentity, applying fallbacks as needed."""
    if isinstance(team, TeamIdentity) and team:
        # Ensure tricode is populated
        if not team.tricode and fallback_tricode:
            return team.model_copy(update={"tricode": fallback_tricode})
        return team
    # Fallback to static lookup, then override name if provided
    identity = _get_team_identity(fallback_tricode, league)
    if fallback_name and fallback_name != identity.name:
        return identity.model_copy(update={"name": fallback_name})
    return identity


def _parse_bet_selection(selection: str) -> tuple[str, str]:
    """Parse bet selection string to extract team and type.

    Args:
        selection: Selection string from BetExecutedPayload

    Returns:
        Tuple of (team, bet_type)

    Examples:
        "LAL_ML" -> ("LAL", "moneyline")
        "LAL_SPREAD_-3.5" -> ("LAL", "spread")
        "OVER_220.5" -> ("OVER", "total")
    """
    parts = selection.split("_")
    if len(parts) == 2 and parts[1] == "ML":
        return parts[0], "moneyline"
    elif len(parts) >= 2 and parts[1] == "SPREAD":
        return parts[0], "spread"
    elif parts[0] in ("OVER", "UNDER"):
        return parts[0], "total"
    else:
        # Default fallback
        return parts[0] if parts else selection, "moneyline"


async def _extract_bets_for_trial(
    trace_reader: TraceReader,
    trial_id: str,
    limit: int = 10,
) -> list["BetSummary"]:
    """Extract recent bets from broker.bet spans for a specific trial.

    Args:
        trace_reader: TraceReader for querying SLS
        trial_id: Trial ID to query
        limit: Maximum number of bets to return

    Returns:
        List of recent bets formatted as BetSummary
    """
    from dojozero.arena_server._models import BetSummary
    from dojozero.betting import BetExecutedPayload

    try:
        # Query broker.bet spans
        spans = await trace_reader.get_spans(
            trial_id,
            operation_names=["broker.bet"],
        )
    except Exception as e:
        LOGGER.warning("Failed to get broker.bet spans for trial '%s': %s", trial_id, e)
        return []

    bets: list[BetSummary] = []
    for span in spans:
        typed = deserialize_span(span)
        if not isinstance(typed, BetExecutedPayload):
            continue

        # Get agent info from cache
        agent_info = await get_cached_agent(trace_reader, typed.agent_id, trial_id)
        if agent_info is None:
            # Fallback: create minimal AgentInfo
            from dojozero.betting import AgentInfo

            LOGGER.warning(
                "AgentInfo for agent_id '%s' not found in cache. Using fallback.",
                typed.agent_id,
            )
            agent_info = AgentInfo(agent_id=typed.agent_id, persona=typed.agent_id)

        # Parse selection to extract team and type
        team, bet_type = _parse_bet_selection(typed.selection)

        try:
            amount = float(typed.amount)
        except (ValueError, TypeError):
            amount = 0.0

        bets.append(
            BetSummary(
                agent=agent_info,
                team=team,
                amount=amount,
                type=bet_type,
            )
        )

    # Return most recent bets (limited)
    return bets[-limit:] if bets else []


async def _extract_games_from_trials(
    trace_reader: TraceReader,
    trial_ids: list[str],
    cache: "LandingPageCache | None" = None,
) -> GamesResponse:
    """Extract games list from trials for landing page.

    Args:
        trace_reader: Trace reader for fetching spans
        trial_ids: List of trial IDs to process
        cache: Optional cache for trial info (uses cache if available, fetches if not)
    """
    live_games: list[GameCardData] = []
    completed_games: list[GameCardData] = []

    for trial_id in trial_ids:
        try:
            # Try cache first, then fetch if not cached
            trial_info = None
            if cache is not None:
                trial_info = cache.get_trial_info(trial_id)

            if trial_info is None:
                trial_info = await _extract_trial_info_from_traces(
                    trace_reader, trial_id
                )
                # Store in cache if available
                if cache is not None:
                    cache.set_trial_info(trial_id, trial_info)
        except Exception as e:
            LOGGER.warning("Failed to get info for trial '%s': %s", trial_id, e)
            continue

        phase = trial_info["phase"]
        metadata = trial_info["metadata"]
        # Normalize league to uppercase for frontend compatibility
        league = metadata.get("sport_type", "NBA").upper()

        home_tricode = metadata.get("home_team_tricode", "TBD")
        away_tricode = metadata.get("away_team_tricode", "TBD")

        # Prefer rich team data from GameInitializeEvent (full TeamIdentity)
        game_init = trial_info.get("game_init")
        if isinstance(game_init, GameInitializeEvent):
            home_team = _resolve_team_identity(
                game_init.home_team, home_tricode, "", league
            )
            away_team = _resolve_team_identity(
                game_init.away_team, away_tricode, "", league
            )
        else:
            home_team = _resolve_team_identity(
                "", home_tricode, metadata.get("home_team_name", ""), league
            )
            away_team = _resolve_team_identity(
                "", away_tricode, metadata.get("away_team_name", ""), league
            )

        # Fetch bets for live games only (performance optimization)
        bets = []
        if phase == "running":
            try:
                bets = await _extract_bets_for_trial(trace_reader, trial_id, limit=10)
            except Exception as e:
                LOGGER.warning("Failed to get bets for trial '%s': %s", trial_id, e)

        # Map phase to frontend status
        status = (
            "live"
            if phase == "running"
            else "completed"
            if phase in ("completed", "stopped")
            else phase
        )

        game_card = GameCardData(
            id=trial_id,
            league=league,
            home_team=home_team,
            away_team=away_team,
            home_score=metadata.get("home_score", 0),
            away_score=metadata.get("away_score", 0),
            status=status,
            date=metadata.get("game_date", ""),
            quarter=metadata.get("quarter", "") if phase == "running" else "",
            clock=metadata.get("clock", "") if phase == "running" else "",
            bets=bets,
            winner=metadata.get("winner_agent")
            if phase in ("completed", "stopped")
            else None,
            win_amount=metadata.get("win_amount", 0)
            if phase in ("completed", "stopped")
            else 0,
        )

        if phase == "running":
            live_games.append(game_card)
        elif phase in ("completed", "stopped"):
            completed_games.append(game_card)

    return GamesResponse(
        live_games=live_games,
        completed_games=completed_games,
    )


async def _extract_agent_actions(
    trace_reader: TraceReader,
    trial_ids: list[str],
    limit: int = 20,
    max_trials: int = 5,
) -> list[AgentAction]:
    """Extract recent agent actions from trial spans.

    Queries agent.response spans from recent games (both live and completed) and returns
    AgentAction objects with agent info, response message, and timestamp.

    Args:
        trace_reader: TraceReader for querying SLS/Jaeger
        trial_ids: List of trial IDs to check for actions
        limit: Maximum number of actions to return
        max_trials: Maximum number of trials to query (reduces SLS load)

    Returns:
        List of agent actions sorted by time (newest first)
    """
    all_actions: list[AgentAction] = []

    # Check recent trials for agent actions (both live and completed games)
    LOGGER.debug(
        "Extracting agent actions from %d trials (limit=%d, max_trials=%d)",
        min(len(trial_ids), max_trials),
        limit,
        max_trials,
    )

    for trial_id in trial_ids[:max_trials]:
        try:
            # Get agent.response spans from the entire trial
            # No time filter - we want recent actions from any games (live or completed)
            spans = await trace_reader.get_spans(
                trial_id,
                operation_names=["agent.response"],
            )
            LOGGER.debug(
                "Trial %s: fetched %d agent.response spans", trial_id, len(spans)
            )
        except Exception as e:
            LOGGER.warning("Failed to get spans for trial '%s': %s", trial_id, e)
            continue

        for span in spans:
            typed = deserialize_span(span)
            if not isinstance(typed, AgentResponseMessage):
                continue

            agent_id = typed.agent_id
            if not agent_id:
                continue

            # Get agent info from cache (lazy loading)
            agent_info = await get_cached_agent(trace_reader, agent_id, trial_id)
            if agent_info is None:
                # Fallback: create minimal AgentInfo
                agent_info = AgentInfo(agent_id=agent_id, persona=agent_id)

            all_actions.append(
                AgentAction(
                    agent=agent_info,
                    response=typed,
                    timestamp=span.start_time,
                )
            )

        # Early exit if we have enough actions (optimization)
        if len(all_actions) >= limit * 2:
            LOGGER.debug("Early exit: collected %d actions", len(all_actions))
            break

    # Sort by timestamp (newest first) and limit
    all_actions.sort(key=lambda x: x.timestamp, reverse=True)
    result = all_actions[:limit]
    LOGGER.debug(
        "Returning %d actions (from %d total)",
        len(result),
        len(all_actions),
    )
    return result


async def _compute_stats(
    trace_reader: TraceReader,
    trial_ids: list[str],
    cache: "LandingPageCache | None" = None,
) -> StatsResponse:
    """Compute aggregate stats for landing page.

    Args:
        trace_reader: Trace reader for fetching spans
        trial_ids: List of trial IDs to process
        cache: Optional cache for trial info (uses cache if available, fetches if not)
    """
    games_played = 0
    live_now = 0
    wagered_today = 0.0

    for trial_id in trial_ids:
        try:
            # Try cache first, then fetch if not cached
            trial_info = None
            if cache is not None:
                trial_info = cache.get_trial_info(trial_id)

            if trial_info is None:
                trial_info = await _extract_trial_info_from_traces(
                    trace_reader, trial_id
                )
                # Store in cache if available
                if cache is not None:
                    cache.set_trial_info(trial_id, trial_info)
        except Exception:
            continue

        phase = trial_info["phase"]

        if phase == "completed":
            games_played += 1
        elif phase == "running":
            live_now += 1

        # Sum wagered amount from metadata if available
        metadata = trial_info.get("metadata", {})
        wagered = metadata.get("total_wagered", 0)
        if wagered:
            wagered_today += float(wagered)

    return StatsResponse(
        games_played=games_played,
        live_now=live_now,
        wagered_today=int(wagered_today),
        total_agents=len(_AGENT_CACHE),
    )


async def _compute_leaderboard(
    trace_reader: TraceReader,
    trial_ids: list[str],
    limit: int = 20,
) -> list[LeaderboardEntry]:
    """Compute agent leaderboard from trial results.

    Uses broker.final_stats spans when available for accurate statistics.
    Falls back to counting agent.response spans if final_stats not found.

    Returns:
        List of agents sorted by winnings (highest first)
    """
    from dojozero.betting import StatisticsList

    # Accumulator for per-agent stats
    @dataclass
    class _AgentStats:
        agent: AgentInfo
        winnings: float = 0.0
        wins: int = 0
        total_bets: int = 0
        total_wagered: float = 0.0

    agent_stats: dict[str, _AgentStats] = {}

    for trial_id in trial_ids:
        try:
            # Try to get broker.final_stats first (most accurate)
            spans = await trace_reader.get_spans(
                trial_id,
                operation_names=["broker.final_stats"],
            )

            if spans:
                # Use final_stats if available
                for span in spans:
                    typed = deserialize_span(span)
                    if isinstance(typed, StatisticsList):
                        for agent_id, stats in typed.statistics.items():
                            agent_info = await get_cached_agent(
                                trace_reader, agent_id, trial_id
                            )
                            if agent_info is None:
                                agent_info = AgentInfo(
                                    agent_id=agent_id, persona=agent_id
                                )

                            if agent_id not in agent_stats:
                                agent_stats[agent_id] = _AgentStats(agent=agent_info)

                            acc = agent_stats[agent_id]
                            acc.winnings += float(stats.net_profit)
                            acc.wins += stats.wins
                            acc.total_bets += stats.total_bets
                            acc.total_wagered += float(stats.total_wagered)
            else:
                # Fallback: count from agent.response spans
                response_spans = await trace_reader.get_spans(
                    trial_id,
                    operation_names=["agent.response"],
                )

                for span in response_spans:
                    typed = deserialize_span(span)
                    if not isinstance(typed, AgentResponseMessage):
                        continue

                    agent_id = typed.agent_id
                    if not agent_id:
                        continue

                    if agent_id not in agent_stats:
                        agent_info = await get_cached_agent(
                            trace_reader, agent_id, trial_id
                        )
                        if agent_info is None:
                            agent_info = AgentInfo(agent_id=agent_id, persona=agent_id)
                        agent_stats[agent_id] = _AgentStats(agent=agent_info)

                    acc = agent_stats[agent_id]
                    if typed.bet_amount:
                        acc.total_bets += 1
                        acc.total_wagered += typed.bet_amount

        except Exception as e:
            LOGGER.warning(
                "Failed to get spans for leaderboard from trial '%s': %s",
                trial_id,
                e,
            )
            continue

    # Convert to sorted list
    leaderboard: list[LeaderboardEntry] = []
    for stats in agent_stats.values():
        win_rate = (stats.wins / stats.total_bets * 100) if stats.total_bets > 0 else 0
        roi = (
            (stats.winnings / stats.total_wagered * 100)
            if stats.total_wagered > 0
            else 0
        )

        leaderboard.append(
            LeaderboardEntry(
                agent=stats.agent,
                winnings=round(stats.winnings, 2),
                winRate=round(win_rate, 1),
                totalBets=stats.total_bets,
                roi=round(roi, 1),
            )
        )

    # Sort by winnings (descending) and add rank
    leaderboard.sort(key=lambda x: x.winnings, reverse=True)
    ranked = [
        entry.model_copy(update={"rank": i + 1})
        for i, entry in enumerate(leaderboard[:limit])
    ]

    return ranked


def _compute_replay_meta(
    items: list[dict[str, Any]],
    core_categories: list[str],
) -> ReplayMetaInfo:
    """Compute replay metadata from serialized items.

    Scans through items once to build:
    - play_item_indices: mapping from play_index to item_index
    - periods: list of PeriodInfo with play counts per period

    Args:
        items: List of serialized span dicts with "category" and "data" keys
        core_categories: Categories to count as "plays" (e.g., ["play"])

    Returns:
        ReplayMetaInfo with pre-computed indices and period info
    """
    play_item_indices: list[int] = []
    period_play_counts: dict[int, int] = {}  # period -> play count
    period_start_indices: dict[int, int] = {}  # period -> first play index

    current_period: int = 1  # Default period

    for item_index, item in enumerate(items):
        category = item.get("category", "")
        data = item.get("data", {})

        # Track core category items (plays)
        if category in core_categories:
            play_index = len(play_item_indices)
            play_item_indices.append(item_index)

            # Get period from play data
            period = data.get("period")
            if period is not None and isinstance(period, int):
                current_period = period

            # Track period stats
            if current_period not in period_play_counts:
                period_play_counts[current_period] = 0
                period_start_indices[current_period] = play_index
            period_play_counts[current_period] += 1

    # Build sorted periods list
    periods: list[PeriodInfo] = []
    for period in sorted(period_play_counts.keys()):
        periods.append(
            PeriodInfo(
                period=period,
                play_count=period_play_counts[period],
                start_play_index=period_start_indices[period],
            )
        )

    return ReplayMetaInfo(
        total_play_count=len(play_item_indices),
        play_item_indices=play_item_indices,
        periods=periods,
    )


async def _load_replay_data(
    trace_reader: TraceReader,
    replay_cache: ReplayCache,
    trial_id: str,
) -> tuple[ReplayCacheEntry | None, ReplayErrorReason | Literal[""]]:
    """Load replay data for a trial.

    Returns:
        Tuple of (cache_entry, error_reason)
        - If successful: (ReplayCacheEntry, "")
        - If failed: (None, reason)

    Reasons:
        - "trial_not_found": No spans found for trial
        - "trial_still_running": Trial hasn't ended yet
        - "no_data": Trial exists but no spans to replay
    """
    # 1. Check cache first
    cached = await replay_cache.get(trial_id)
    if cached:
        return cached, ""

    # 2. Fetch from trace store
    try:
        spans = await trace_reader.get_spans(trial_id)
    except Exception as e:
        LOGGER.error("Failed to fetch spans for replay: %s", e)
        return None, "trial_not_found"

    if not spans:
        return None, "trial_not_found"

    # 3. Check if trial has ended
    has_ended = False
    items: list[dict[str, Any]] = []

    for span in spans:
        typed = deserialize_span(span)
        if typed is None:
            continue

        # Serialize for WS
        items.append(serialize_span_for_ws(typed))

        # Check for end marker
        if isinstance(typed, TrialLifecycleSpan):
            if typed.phase in ("stopped", "terminated"):
                has_ended = True

    if not has_ended:
        LOGGER.info("Trial %s has not ended yet, replay unavailable", trial_id)
        return None, "trial_still_running"

    if not items:
        return None, "no_data"

    # 4. Compute metadata and cache the result
    meta = _compute_replay_meta(items, replay_cache.core_categories)
    await replay_cache.set(trial_id, items, meta)

    # Return a fresh entry (same as what we just cached)
    return ReplayCacheEntry(items=items, meta=meta), ""


def create_arena_app(
    trace_backend: str,
    trace_query_endpoint: str | None = None,
    static_dir: Path | None = None,
    poll_interval: float = 1.0,
    service_name: str = "dojozero",
    by_alias: bool = True,
) -> FastAPI:
    """Create the Arena Server FastAPI application.

    Args:
        trace_backend: Trace backend type ("jaeger" or "sls")
        trace_query_endpoint: Jaeger Query API endpoint (only used when trace_backend="jaeger")
        static_dir: Path to static files (React build output)
        poll_interval: Interval for polling new spans
        service_name: Service name for Jaeger or SLS trace backend (use --service-name)
        by_alias: Use serialization aliases (camelCase) in REST JSON responses.
            True (default) outputs camelCase keys; False outputs snake_case keys.

    For SLS backend, configuration comes from environment variables:
        DOJOZERO_SLS_PROJECT: SLS project name
        DOJOZERO_SLS_ENDPOINT: SLS endpoint (e.g., cn-hangzhou.log.aliyuncs.com)
        DOJOZERO_SLS_LOGSTORE: Logstore name (e.g., "dojozero-traces")
    """
    trace_reader = create_trace_reader(
        backend=trace_backend,
        trace_query_endpoint=trace_query_endpoint,
        service_name=service_name,  # Used for both Jaeger and SLS backends
    )
    broadcaster = SpanBroadcaster()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _server_state

        # Create cache and refresher
        cache = LandingPageCache()
        replay_cache = ReplayCache()
        refresher = BackgroundRefresher(
            trace_reader=trace_reader,
            cache=cache,
            replay_cache=replay_cache,
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
    # REST Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/trials")
    async def list_trials(
        start_time: int | None = Query(
            default=None,
            description="Start time as Unix timestamp (seconds). Defaults to 7 days ago.",
        ),
        end_time: int | None = Query(
            default=None,
            description="End time as Unix timestamp (seconds). Defaults to now.",
        ),
        limit: int = Query(
            default=500,
            description="Maximum number of trials to return.",
            ge=1,
            le=1000,
        ),
    ) -> JSONResponse:
        """List trials with metadata extracted from traces.

        Query Parameters:
            start_time: Start time as Unix timestamp in seconds (default: 7 days ago)
            end_time: End time as Unix timestamp in seconds (default: now)
            limit: Maximum number of trials to return (default: 500, max: 1000)
        """
        state = get_server_state()

        # Convert Unix timestamps to datetime
        start_dt = (
            datetime.fromtimestamp(start_time, tz=timezone.utc)
            if start_time is not None
            else None
        )
        end_dt = (
            datetime.fromtimestamp(end_time, tz=timezone.utc)
            if end_time is not None
            else None
        )

        # Get trial list from trace store with time range filter
        trial_ids = await state.trace_reader.list_trials(
            start_time=start_dt,
            end_time=end_dt,
            limit=limit,
        )

        # Build result with phase and metadata extracted from traces
        result: list[TrialListItem] = []
        for tid in trial_ids:
            trial_info_extracted = await _extract_trial_info_from_traces(
                state.trace_reader, tid
            )
            result.append(
                TrialListItem(
                    id=tid,
                    phase=trial_info_extracted["phase"],
                    metadata=trial_info_extracted.get("metadata", {}),
                )
            )

        return JSONResponse(
            content=[item.model_dump(by_alias=state.by_alias) for item in result]
        )

    @app.get("/api/trials/{trial_id}")
    async def get_trial(trial_id: str) -> JSONResponse:
        """Get trial info and spans.

        Data is served from cache (background refresh keeps it fresh for live trials).
        On cache miss, triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        start_time_ts = time.time()
        LOGGER.info("Fetching trial details for: %s", trial_id)

        # Get trial details from cache
        cached = state.cache.get_trial_details(trial_id)
        if cached is not None:
            items = cached.get("items", [])
        else:
            # Cache miss - refresh on demand
            items = await refresher.refresh_trial_details_on_demand(trial_id)

        elapsed = time.time() - start_time_ts
        LOGGER.info(
            "Trial %s: Returned %d items in %.2fs",
            trial_id,
            len(items) if items else 0,
            elapsed,
        )

        if not items:
            # Check if trial exists (may have no spans yet)
            trial_ids = state.cache.get_trials_list() or []
            if trial_id not in trial_ids:
                return JSONResponse(
                    content={"error": f"Trial '{trial_id}' not found"},
                    status_code=404,
                )

        response = TrialDetailResponse(trial_id=trial_id, items=items)
        return JSONResponse(content=response.model_dump(by_alias=state.by_alias))

    # -------------------------------------------------------------------------
    # Landing Page Endpoints (with caching)
    # -------------------------------------------------------------------------

    @app.get("/api/landing")
    async def get_landing_data(
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc. Returns all if not specified.",
        ),
    ) -> JSONResponse:
        """Get aggregated landing page data.

        Returns games, stats, and recent agent actions in a single call.
        Data is served from cache (background refresh keeps it fresh).

        On cache miss (new data not yet in cache), triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        # Get stats from cache, or refresh on demand
        stats = state.cache.get_stats(league=league)
        if stats is None:
            stats = await refresher.refresh_stats_on_demand(league=league)

        # Get games from cache, or refresh on demand
        games = state.cache.get_games(league=league)
        if games is None:
            games = await refresher.refresh_games_on_demand(league=league)

        # Get agent actions from cache, or refresh on demand
        agent_actions = state.cache.get_agent_actions(league=league)
        if agent_actions is None:
            agent_actions = await refresher.refresh_agent_actions_on_demand(
                league=league
            )

        all_games = games.live_games + games.upcoming_games + games.completed_games
        # NOTE! Fallback: use all_games if live_games is empty! For temporary use
        live_games = games.live_games if games.live_games else all_games
        response = LandingResponse(
            stats=stats,
            live_games=live_games,
            all_games=all_games,
            live_agent_actions=agent_actions,
        )
        return JSONResponse(content=response.model_dump(by_alias=state.by_alias))

    @app.get("/api/stats")
    async def get_stats(
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc. Returns all if not specified.",
        ),
    ) -> JSONResponse:
        """Get real-time stats for the hero section.

        Returns:
            gamesPlayed: Total completed games
            liveNow: Currently running games
            wageredToday: Total amount wagered (if available)

        Data is served from cache (background refresh keeps it fresh).
        On cache miss, triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        # Get stats from cache, or refresh on demand
        stats = state.cache.get_stats(league=league)
        if stats is None:
            stats = await refresher.refresh_stats_on_demand(league=league)

        return JSONResponse(content=stats.model_dump(by_alias=state.by_alias))

    @app.get("/api/games")
    async def get_games(
        status: str | None = Query(
            default=None,
            description="Filter by status: 'live', 'upcoming', 'completed', or 'all'.",
        ),
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc.",
        ),
        limit: int = Query(
            default=50,
            description="Maximum number of games to return.",
            ge=1,
            le=200,
        ),
    ) -> JSONResponse:
        """Get games list with optional filters.

        Returns games grouped by status or filtered by query params.
        Data is served from cache (background refresh keeps it fresh).
        On cache miss, triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        # Get games from cache, or refresh on demand
        games_data = state.cache.get_games(league=league)
        if games_data is None:
            games_data = await refresher.refresh_games_on_demand(league=league)

        # Apply status filter
        all_games: list[GameCardData] = (
            games_data.live_games
            + games_data.upcoming_games
            + games_data.completed_games
        )

        if status:
            status_map = {
                "live": "running",
                "upcoming": "upcoming",
                "completed": "completed",
            }
            target_status = status_map.get(status, status)
            all_games = [g for g in all_games if g.status == target_status]

        filtered = all_games[:limit]
        return JSONResponse(
            content={
                "games": [g.model_dump(by_alias=state.by_alias) for g in filtered],
                "total": len(all_games),
            }
        )

    @app.get("/api/leaderboard")
    async def get_leaderboard(
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc.",
        ),
        limit: int = Query(
            default=20,
            description="Maximum number of agents to return.",
            ge=1,
            le=100,
        ),
    ) -> JSONResponse:
        """Get agent leaderboard ranked by winnings.

        Returns agents sorted by total winnings with win rate and ROI.
        Data is served from cache (background refresh keeps it fresh).
        On cache miss, triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        # Get leaderboard from cache, or refresh on demand
        leaderboard = state.cache.get_leaderboard(league=league)
        if leaderboard is None:
            leaderboard = await refresher.refresh_leaderboard_on_demand(league=league)

        # Apply limit
        leaderboard = leaderboard[:limit]

        response = LeaderboardResponse(leaderboard=leaderboard)
        return JSONResponse(
            content=response.model_dump(by_alias=state.by_alias),
        )

    @app.get("/api/agent-actions")
    async def get_agent_actions(
        limit: int = Query(
            default=20,
            description="Maximum number of actions to return.",
            ge=1,
            le=100,
        ),
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc. Returns all if not specified.",
        ),
    ) -> JSONResponse:
        """Get recent agent actions for the live ticker.

        Returns the most recent agent actions sorted by time.
        Data is served from cache (background refresh keeps it fresh).
        On cache miss, triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        # Get agent actions from cache, or refresh on demand
        actions = state.cache.get_agent_actions(league=league)
        if actions is None:
            actions = await refresher.refresh_agent_actions_on_demand(league=league)

        # Apply limit
        actions = actions[:limit]

        response = AgentActionsResponse(actions=actions)
        return JSONResponse(
            content=response.model_dump(by_alias=state.by_alias),
        )

    # -------------------------------------------------------------------------
    # WebSocket Endpoint for Real-time Streaming
    # -------------------------------------------------------------------------

    @app.websocket("/ws/trials/{trial_id}/stream")
    async def trial_stream(
        websocket: WebSocket,
        trial_id: str,
        categories: str | None = Query(
            default=None,
            description="Comma-separated categories to filter (e.g., 'play,game_update'). "
            "If not specified, all categories are included.",
        ),
        filter_mode: str = Query(
            default="include",
            description="Filter mode: 'include' or 'exclude'.",
        ),
    ):
        """WebSocket endpoint for real-time span streaming with pause/resume and filtering.

        Uses cached trial_details (refreshed by background task every 5s for live trials).
        On cache miss, triggers on-demand refresh.

        Protocol:
        - Server sends 'snapshot' immediately upon connection
        - Server pushes 'span' messages as new spans are detected in cache
        - Server sends 'trial_ended' when trial completes
        - Server sends 'heartbeat' periodically
        - Server sends 'stream_status' in response to control commands

        Control commands (send as JSON):
            {"command": "pause"}   - Pause streaming (buffers items)
            {"command": "resume"}  - Resume streaming (sends buffered items)
            {"command": "status"}  - Get current stream status
            {"command": "filter", "categories": [...], "mode": "include"}
                                   - Update category filter dynamically
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        await websocket.accept()
        LOGGER.info("WebSocket connection accepted for trial '%s'", trial_id)

        # Per-connection stream controller (stores items during pause, not raw spans)
        controller = StreamController()

        # Initialize category filter from query params
        cat_filter = CategoryFilter.from_query(categories, filter_mode)

        try:
            await state.broadcaster.subscribe(trial_id, websocket)

            # Get initial snapshot from cache (or refresh on demand)
            cached = state.cache.get_trial_details(trial_id)
            if cached is None:
                # Cache miss - refresh on demand
                await refresher.refresh_trial_details_on_demand(trial_id)
                cached = state.cache.get_trial_details(trial_id)

            all_items = cached.get("items", []) if cached else []
            is_completed = cached.get("is_completed", False) if cached else False

            # Filter and send snapshot
            snapshot_items = cat_filter.filter_items(all_items)
            snapshot_msg = WSSnapshotMessage(
                trial_id=trial_id,
                timestamp=datetime.now(timezone.utc),
                data={"items": snapshot_items},
            )
            await websocket.send_text(snapshot_msg.model_dump_json())
            LOGGER.info(
                "Stream: Sent snapshot with %d items (filtered from %d) for trial '%s'",
                len(snapshot_items),
                len(all_items),
                trial_id,
            )

            # Track how many items we've sent (to detect new items in cache)
            items_sent_count = len(all_items)

            # If already completed, send trial_ended and close
            if is_completed:
                ended_msg = WSTrialEndedMessage(
                    trial_id=trial_id,
                    timestamp=datetime.now(timezone.utc),
                )
                await websocket.send_text(ended_msg.model_dump_json())
                LOGGER.info("Trial '%s' already completed, closing stream", trial_id)
                return

            # Pause buffer: stores items (not raw spans)
            pause_buffer: list[dict[str, Any]] = []

            # Poll for new items in cache
            while True:
                try:
                    # Wait for client message or timeout
                    msg_text = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=state.poll_interval,
                    )

                    # Handle control commands
                    try:
                        command_data = json.loads(msg_text)
                        command = command_data.get("command", "")

                        if command == "pause":
                            controller.pause()
                            status_msg = WSStreamStatusMessage(
                                is_paused=True,
                                buffer_size=len(pause_buffer),
                                timestamp=datetime.now(timezone.utc),
                            )
                            await websocket.send_text(status_msg.model_dump_json())

                        elif command == "resume":
                            controller.resume()
                            # Send buffered items as snapshot (catch-up mode)
                            if pause_buffer:
                                filtered_buffered = cat_filter.filter_items(pause_buffer)
                                if filtered_buffered:
                                    snapshot_msg = WSSnapshotMessage(
                                        trial_id=trial_id,
                                        timestamp=datetime.now(timezone.utc),
                                        data={"items": filtered_buffered},
                                    )
                                    await websocket.send_text(
                                        snapshot_msg.model_dump_json()
                                    )
                            buffered_count = len(pause_buffer)
                            pause_buffer = []
                            status_msg = WSStreamStatusMessage(
                                is_paused=False,
                                buffered_count=buffered_count,
                                timestamp=datetime.now(timezone.utc),
                            )
                            await websocket.send_text(status_msg.model_dump_json())

                        elif command == "filter":
                            # Update category filter dynamically
                            filter_categories = command_data.get("categories", [])
                            filter_mode_cmd = command_data.get("mode", "include")
                            cat_filter = CategoryFilter.from_list(
                                filter_categories, filter_mode_cmd
                            )
                            LOGGER.debug(
                                "Stream: Updated filter for trial '%s': categories=%s, mode=%s",
                                trial_id,
                                filter_categories,
                                filter_mode_cmd,
                            )
                            status_msg = WSStreamStatusMessage(
                                is_paused=controller.is_paused,
                                buffer_size=len(pause_buffer),
                                timestamp=datetime.now(timezone.utc),
                            )
                            await websocket.send_text(status_msg.model_dump_json())

                        elif command == "status":
                            status_msg = WSStreamStatusMessage(
                                is_paused=controller.is_paused,
                                buffer_size=len(pause_buffer),
                                timestamp=datetime.now(timezone.utc),
                            )
                            await websocket.send_text(status_msg.model_dump_json())

                    except json.JSONDecodeError:
                        LOGGER.warning("Invalid JSON command: %s", msg_text)

                except asyncio.TimeoutError:
                    # Check cache for new items
                    cached = state.cache.get_trial_details(trial_id)
                    if cached is None:
                        # Cache was invalidated, skip this cycle
                        pass
                    else:
                        current_items = cached.get("items", [])
                        is_now_completed = cached.get("is_completed", False)

                        # Check for new items
                        if len(current_items) > items_sent_count:
                            new_items = current_items[items_sent_count:]

                            for item in new_items:
                                if controller.is_paused:
                                    # Buffer during pause
                                    if len(pause_buffer) < 1000:  # Max buffer size
                                        pause_buffer.append(item)
                                else:
                                    # Send immediately (with category filter)
                                    if not cat_filter.filter_item(item):
                                        continue

                                    message = WSSpanMessage(
                                        trial_id=trial_id,
                                        timestamp=datetime.now(timezone.utc),
                                        category=item.get("category", ""),
                                        data=item.get("data", {}),
                                    )
                                    await websocket.send_text(message.model_dump_json())

                            items_sent_count = len(current_items)

                            if not controller.is_paused:
                                LOGGER.debug(
                                    "Sent %d new items for trial '%s'",
                                    len(new_items),
                                    trial_id,
                                )

                        # Check if trial just completed
                        if is_now_completed:
                            ended_msg = WSTrialEndedMessage(
                                trial_id=trial_id,
                                timestamp=datetime.now(timezone.utc),
                            )
                            await websocket.send_text(ended_msg.model_dump_json())
                            LOGGER.info("Trial '%s' completed, closing stream", trial_id)
                            return

                    # Send heartbeat (even when paused, to keep connection alive)
                    heartbeat = WSHeartbeatMessage(
                        timestamp=datetime.now(timezone.utc),
                    )
                    await websocket.send_text(heartbeat.model_dump_json())

        except WebSocketDisconnect:
            LOGGER.info("WebSocket disconnected for trial '%s'", trial_id)
        except Exception as e:
            LOGGER.error("WebSocket error for trial '%s': %s", trial_id, e)
        finally:
            await state.broadcaster.unsubscribe(trial_id, websocket)

    @app.websocket("/ws/trials/{trial_id}/replay")
    async def trial_replay(
        websocket: WebSocket,
        trial_id: str,
        autostart: bool = Query(
            default=True,
            description="Auto-start playback on connection. If false, waits for resume.",
        ),
        snapshot_size: int = Query(
            default=20,
            description="Number of items to include in snapshot.",
            ge=1,
            le=200,
        ),
        categories: str | None = Query(
            default=None,
            description="Comma-separated categories to filter (e.g., 'play,game_update'). "
            "If not specified, all categories are included.",
        ),
        filter_mode: str = Query(
            default="include",
            description="Filter mode: 'include' or 'exclude'.",
        ),
    ):
        """WebSocket endpoint for replaying completed trials.

        Only works for trials that have ended (trial.stopped/terminated).
        Replays at uniform speed with support for pause/resume, speed control, seeking,
        and category filtering.

        Query Parameters:
            autostart: If false, only sends meta_info; waits for resume to start (default: true)
            snapshot_size: Number of items in snapshot (default: 20)
            categories: Comma-separated categories to filter (e.g., 'play,game_update')
            filter_mode: 'include' or 'exclude' (default: 'include')

        Control commands (send as JSON):
            {"command": "pause"}                     - Pause replay
            {"command": "resume"}                    - Resume replay (also starts if autostart=false)
            {"command": "speed", "value": 2}         - Set speed (1, 2, 4, 10, or 20)
            {"command": "reset"}                     - Restart from beginning
            {"command": "seek", "play_index": N}     - Seek to play index N (0-based)
            {"command": "status"}                    - Get current status
            {"command": "filter", "categories": [...], "mode": "include"}
                                                     - Update category filter dynamically

        Server messages:
            {"type": "replay_meta_info", ...}       - Metadata (sent first, always)
            {"type": "snapshot", ...}               - Batch of events
            {"type": "span", ...}                   - Single event during playback
            {"type": "replay_status", ...}          - Playback status update
            {"type": "replay_unavailable", ...}     - Replay not available
            {"type": "trial_ended", ...}            - End of replay
            {"type": "heartbeat", ...}              - Keepalive (fixed interval)
        """
        state = get_server_state()
        await websocket.accept()
        LOGGER.info(
            "Replay WebSocket connection accepted for trial '%s' (autostart=%s, snapshot_size=%d)",
            trial_id,
            autostart,
            snapshot_size,
        )

        # Load replay data (includes pre-computed meta)
        cache_entry, error_reason = await _load_replay_data(
            state.trace_reader,
            state.replay_cache,
            trial_id,
        )

        if cache_entry is None:
            # Send unavailable message and close
            unavailable_msg = WSReplayUnavailableMessage(
                trial_id=trial_id,
                reason=cast(ReplayErrorReason, error_reason),
                timestamp=datetime.now(timezone.utc),
            )
            await websocket.send_text(unavailable_msg.model_dump_json())
            await websocket.close()
            LOGGER.info("Replay unavailable for trial '%s': %s", trial_id, error_reason)
            return

        # Create controller with pre-computed meta
        controller = TrialReplayController(
            trial_id=trial_id,
            items=cache_entry.items,
            meta=cache_entry.meta,
            snapshot_size=snapshot_size,
        )

        # Initialize category filter from query params
        cat_filter = CategoryFilter.from_query(categories, filter_mode)

        try:
            # 1. Always send meta info first
            meta_msg = WSReplayMetaInfoMessage(
                trial_id=trial_id,
                total_items=len(cache_entry.items),
                total_play_count=cache_entry.meta.total_play_count,
                periods=[
                    {
                        "period": p.period,
                        "playCount": p.play_count,
                        "startPlayIndex": p.start_play_index,
                    }
                    for p in cache_entry.meta.periods
                ],
                timestamp=datetime.now(timezone.utc),
            )
            await websocket.send_text(meta_msg.model_dump_json())
            LOGGER.info(
                "Replay: Sent meta info for trial '%s' (plays=%d, periods=%d)",
                trial_id,
                cache_entry.meta.total_play_count,
                len(cache_entry.meta.periods),
            )

            # 2. If autostart, send initial snapshot; otherwise pause and wait
            if autostart:
                snapshot_items = controller.get_snapshot_items()
                # Apply category filter to snapshot
                filtered_snapshot = cat_filter.filter_items(snapshot_items)
                snapshot_msg = WSSnapshotMessage(
                    trial_id=trial_id,
                    timestamp=datetime.now(timezone.utc),
                    data={"items": filtered_snapshot},
                )
                await websocket.send_text(snapshot_msg.model_dump_json())
                LOGGER.info(
                    "Replay: Sent snapshot with %d items (filtered from %d) for trial '%s'",
                    len(filtered_snapshot),
                    len(snapshot_items),
                    trial_id,
                )
            else:
                # Pause immediately when autostart=false
                controller.pause()
                LOGGER.info("Replay: Waiting for resume command (autostart=false)")

            # Send initial status
            await websocket.send_text(controller.get_status().model_dump_json())

            # Track last heartbeat time (separate from playback timing)
            last_heartbeat_time = time.time()

            # Main replay loop
            while True:
                # Calculate timeout: use shorter of playback interval and time until next heartbeat
                playback_interval = controller.get_effective_interval()
                time_since_heartbeat = time.time() - last_heartbeat_time
                time_until_heartbeat = max(
                    0, controller.heartbeat_interval - time_since_heartbeat
                )

                # When paused, only wait for heartbeat; when playing, use shorter interval
                if controller.is_paused:
                    timeout = time_until_heartbeat
                else:
                    timeout = min(playback_interval, time_until_heartbeat)

                try:
                    # Wait for command or timeout
                    msg_text = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=max(timeout, 0.1),  # Minimum 100ms to avoid busy loop
                    )

                    # Handle command
                    try:
                        command_data = json.loads(msg_text)
                        command = command_data.get("command", "")
                        value = command_data.get("value")

                        if command == "pause":
                            controller.pause()
                        elif command == "resume":
                            # If first resume after autostart=false, send snapshot
                            if not autostart and controller.current_index == 0:
                                snapshot_items = controller.get_snapshot_items()
                                filtered_snapshot = cat_filter.filter_items(
                                    snapshot_items
                                )
                                snapshot_msg = WSSnapshotMessage(
                                    trial_id=trial_id,
                                    timestamp=datetime.now(timezone.utc),
                                    data={"items": filtered_snapshot},
                                )
                                await websocket.send_text(
                                    snapshot_msg.model_dump_json()
                                )
                            controller.resume()
                        elif command == "speed" and value is not None:
                            controller.set_speed(float(value))
                        elif command == "reset":
                            controller.reset()
                            # Re-send snapshot
                            snapshot_items = controller.get_snapshot_items()
                            filtered_snapshot = cat_filter.filter_items(snapshot_items)
                            snapshot_msg = WSSnapshotMessage(
                                trial_id=trial_id,
                                timestamp=datetime.now(timezone.utc),
                                data={"items": filtered_snapshot},
                            )
                            await websocket.send_text(snapshot_msg.model_dump_json())
                        elif command == "seek":
                            play_index = command_data.get("play_index", 0)
                            seek_items = controller.seek_to_play_index(int(play_index))
                            filtered_seek = cat_filter.filter_items(seek_items)
                            snapshot_msg = WSSnapshotMessage(
                                trial_id=trial_id,
                                timestamp=datetime.now(timezone.utc),
                                data={"items": filtered_seek},
                            )
                            await websocket.send_text(snapshot_msg.model_dump_json())
                            LOGGER.debug(
                                "Replay: Seeked to play_index %d for trial '%s'",
                                play_index,
                                trial_id,
                            )
                        elif command == "filter":
                            # Update category filter dynamically
                            filter_categories = command_data.get("categories", [])
                            filter_mode_cmd = command_data.get("mode", "include")
                            cat_filter = CategoryFilter.from_list(
                                filter_categories, filter_mode_cmd
                            )
                            LOGGER.debug(
                                "Replay: Updated filter for trial '%s': categories=%s, mode=%s",
                                trial_id,
                                filter_categories,
                                filter_mode_cmd,
                            )
                        elif command == "status":
                            pass  # Just send status below
                        else:
                            LOGGER.warning("Unknown replay command: %s", command)
                            continue

                        # Send status after any command
                        await websocket.send_text(
                            controller.get_status().model_dump_json()
                        )

                    except json.JSONDecodeError:
                        LOGGER.warning("Invalid JSON command: %s", msg_text)

                except asyncio.TimeoutError:
                    # Check if we need to send heartbeat (fixed interval)
                    if (
                        time.time() - last_heartbeat_time
                        >= controller.heartbeat_interval
                    ):
                        heartbeat = WSHeartbeatMessage(
                            timestamp=datetime.now(timezone.utc),
                        )
                        await websocket.send_text(heartbeat.model_dump_json())
                        last_heartbeat_time = time.time()

                    # Skip playback logic if paused
                    if controller.is_paused:
                        continue

                    if controller.is_complete():
                        # Send trial ended
                        ended_msg = WSTrialEndedMessage(
                            trial_id=trial_id,
                            timestamp=datetime.now(timezone.utc),
                        )
                        await websocket.send_text(ended_msg.model_dump_json())
                        LOGGER.info("Replay completed for trial '%s'", trial_id)

                        # Pause at end, allow reset/seek
                        controller.pause()
                        await websocket.send_text(
                            controller.get_status().model_dump_json()
                        )
                        continue

                    # Send next item (apply category filter)
                    item = controller.get_next_item()
                    if item:
                        # Skip items that don't match the filter
                        if not cat_filter.filter_item(item):
                            continue

                        span_msg = WSSpanMessage(
                            trial_id=trial_id,
                            timestamp=datetime.now(timezone.utc),
                            category=item.get("category", ""),
                            data=item.get("data", {}),
                        )
                        await websocket.send_text(span_msg.model_dump_json())

                        # Send status every 20 items
                        if controller.current_index % 20 == 0:
                            await websocket.send_text(
                                controller.get_status().model_dump_json()
                            )

        except WebSocketDisconnect:
            LOGGER.info("Replay WebSocket disconnected for trial '%s'", trial_id)
        except Exception as e:
            LOGGER.error("Replay WebSocket error for trial '%s': %s", trial_id, e)

    @app.post("/api/trials/{trial_id}/replay")
    async def get_trial_replay_data(
        trial_id: str,
        categories: str | None = Query(
            default=None,
            description="Comma-separated categories to filter (e.g., 'play,game_update'). "
            "If not specified, all categories are included.",
        ),
        filter_mode: str = Query(
            default="include",
            description="Filter mode: 'include' (only specified categories) or "
            "'exclude' (all except specified categories).",
        ),
    ) -> JSONResponse:
        """Get all replay data for a completed trial at once.

        This endpoint returns all spans for a completed trial in a single response,
        allowing the frontend to implement its own playback logic.

        Only works for trials that have ended (trial.stopped/terminated).

        Query Parameters:
            categories: Comma-separated list of categories to filter
            filter_mode: 'include' or 'exclude'

        Returns:
            ReplayResponse with:
                - available: True if replay data is available
                - reason: Error reason if not available
                - items: List of serialized spans (filtered by category if specified)
                - totalItems: Total number of items after filtering
        """
        state = get_server_state()

        cache_entry, error_reason = await _load_replay_data(
            state.trace_reader,
            state.replay_cache,
            trial_id,
        )

        if cache_entry is None:
            response = ReplayResponse(
                trial_id=trial_id,
                available=False,
                reason=cast(ReplayErrorReason, error_reason),
                items=[],
                total_items=0,
            )
            # Return 200 with available=false rather than 404
            # This allows frontend to handle gracefully
            return JSONResponse(content=response.model_dump(by_alias=state.by_alias))

        # Apply category filter
        cat_filter = CategoryFilter.from_query(categories, filter_mode)
        filtered_items = cat_filter.filter_items(cache_entry.items)

        response = ReplayResponse(
            trial_id=trial_id,
            available=True,
            items=filtered_items,
            reason=None,
            total_items=len(filtered_items),
        )
        return JSONResponse(content=response.model_dump(by_alias=state.by_alias))

    # -------------------------------------------------------------------------
    # Health Check
    # -------------------------------------------------------------------------

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        state = get_server_state()
        return {
            "status": "ok",
            "static_dir": str(state.static_dir) if state.static_dir else None,
        }

    @app.get("/api/cache-stats")
    async def get_cache_stats() -> JSONResponse:
        """Get cache statistics for debugging and monitoring.

        Returns current cache configuration and status of all cache entries.
        Useful for diagnosing caching issues and tuning TTL values.
        """
        state = get_server_state()
        return JSONResponse(content=state.cache.get_cache_stats())

    # -------------------------------------------------------------------------
    # Static File Serving (SPA support)
    # -------------------------------------------------------------------------

    if static_dir and static_dir.exists():
        # Serve static files
        app.mount(
            "/assets",
            StaticFiles(directory=static_dir / "assets"),
            name="assets",
        )

        @app.get("/{path:path}")
        async def serve_spa(path: str):
            """Serve static files with SPA fallback."""
            file_path = static_dir / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            # SPA fallback
            index_path = static_dir / "index.html"
            if index_path.exists():
                return FileResponse(index_path)
            return JSONResponse(
                content={"error": "Not found"},
                status_code=404,
            )

    return app


async def run_arena_server(
    host: str = "127.0.0.1",
    port: int = 3001,
    trace_backend: str = "jaeger",
    trace_query_endpoint: str | None = None,
    static_dir: Path | None = None,
    service_name: str = "dojozero",
    by_alias: bool = True,
) -> None:
    """Run the Arena Server.

    Args:
        host: Host to bind to
        port: Port to listen on
        trace_backend: Trace backend type ("jaeger" or "sls")
        trace_query_endpoint: Jaeger Query API endpoint (only used when trace_backend="jaeger")
        static_dir: Path to static files (React build output)
        service_name: Service name for Jaeger or SLS trace backend (use --service-name)
        by_alias: Use serialization aliases (camelCase) in REST JSON responses.

    For SLS backend, configuration comes from environment variables:
        DOJOZERO_SLS_PROJECT: SLS project name
        DOJOZERO_SLS_ENDPOINT: SLS endpoint (e.g., cn-hangzhou.log.aliyuncs.com)
        DOJOZERO_SLS_LOGSTORE: Logstore name (e.g., "dojozero-traces")
    """
    import uvicorn

    app = create_arena_app(
        trace_backend=trace_backend,
        trace_query_endpoint=trace_query_endpoint,
        static_dir=static_dir,
        service_name=service_name,
        by_alias=by_alias,
    )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


__all__ = [
    "ArenaServerState",
    "BackgroundRefresher",
    "CacheConfig",
    "CacheEntry",
    "CategoryFilter",
    "DEFAULT_CACHE_CONFIG",
    "LandingPageCache",
    "PeriodInfo",
    "ReplayCache",
    "ReplayCacheEntry",
    "ReplayMetaInfo",
    "SpanBroadcaster",
    "StreamController",
    "TrialReplayController",
    "WSMessageType",
    "create_arena_app",
    "create_trace_reader",
    "get_server_state",
    "run_arena_server",
]
