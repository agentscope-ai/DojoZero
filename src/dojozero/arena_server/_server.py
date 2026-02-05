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
- GET  /api/landing                   - Landing page data (games, stats, actions)
- GET  /api/stats                     - Real-time stats (games, wagered, etc.)
- GET  /api/games                     - All games (live, upcoming, completed)
- GET  /api/leaderboard               - Agent rankings by winnings
- GET  /api/agent-actions             - Recent agent actions
- WS   /ws/trials/{trial_id}/stream   - Real-time span streaming

Configuration:
    dojo0 arena --trace-backend sls
    dojo0 arena --trace-backend jaeger --trace-query-endpoint http://localhost:16686
    # Use --service-name to specify the service name for both Jaeger and SLS backends
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dojozero.arena_server._models import (
    AgentActionResponse,
    AgentActionsResponse,
    BetSummary,
    GameCardData,
    GamesResponse,
    LandingResponse,
    LeaderboardResponse,
    StatsResponse,
    TrialDetailResponse,
    TrialListItem,
    WSHeartbeatMessage,
    WSSnapshotMessage,
    WSSpanMessage,
    WSTrialEndedMessage,
)
from dojozero.arena_server._replay import (
    create_replay_websocket_handler,
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
AgentActionResponse.model_rebuild()
BetSummary.model_rebuild()

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
    with the tricode as the name if not found.
    """
    teams = _NBA_TEAMS if league == "NBA" else _NFL_TEAMS
    if tricode in teams:
        return teams[tricode]
    return TeamIdentity(name=tricode, tricode=tricode, color=_DEFAULT_TEAM_COLOR)


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
            timestamp=datetime.now(timezone.utc).isoformat(),
            category=ws_payload.get("category", ""),
            data=ws_payload.get("data", {}),
        )
        await self._send_to_trial(trial_id, message)

    async def broadcast_trial_ended(self, trial_id: str) -> None:
        """Notify all clients that a trial has ended."""
        message = WSTrialEndedMessage(
            trial_id=trial_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
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
            timestamp=datetime.now(timezone.utc).isoformat(),
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


@dataclass
class CacheEntry:
    """A cache entry with data and TTL."""

    data: Any
    expires_at: float  # Unix timestamp

    def is_valid(self) -> bool:
        """Check if the cache entry is still valid."""
        return time.time() < self.expires_at


@dataclass
class LandingPageCache:
    """Cache for landing page data to reduce trace store queries.

    Maintains separate caches for different data types with different TTLs:
    - trials_list: List of all trials (30s TTL)
    - trial_info: Per-trial info with phase/metadata (10s TTL)
    - stats: Aggregated stats (5s TTL)
    - leaderboard: Agent rankings (30s TTL)
    - agent_actions: Recent actions (2s TTL for freshness)
    """

    # TTL values in seconds
    # More aggressive caching to reduce SLS load
    TRIALS_LIST_TTL: float = 60.0  # 1 minute
    TRIAL_INFO_TTL: float = 30.0  # 30 seconds
    TRIAL_DETAILS_TTL: float = 60.0  # 1 minute (for incremental span fetching)
    STATS_TTL: float = 10.0  # 10 seconds
    LEADERBOARD_TTL: float = (
        3600.0  # 1 hour (only changes when games complete, which is infrequent)
    )
    AGENT_ACTIONS_TTL: float = 5.0  # 5 seconds
    GAMES_TTL: float = 15.0  # 15 seconds

    _trials_list: CacheEntry | None = None
    _trial_info: dict[str, CacheEntry] = field(default_factory=dict)
    _trial_details: dict[str, CacheEntry] = field(
        default_factory=dict
    )  # Cache for full trial spans
    _stats: CacheEntry | None = None
    _leaderboard: CacheEntry | None = None
    _agent_actions: CacheEntry | None = None
    _games: CacheEntry | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def get_trials_list(
        self,
        fetcher: Any,  # Callable that returns list of trial IDs
    ) -> list[str]:
        """Get cached trials list or fetch if expired."""
        async with self._lock:
            if self._trials_list is not None and self._trials_list.is_valid():
                LOGGER.debug("Cache hit: trials_list")
                return self._trials_list.data

        # Fetch outside lock to avoid blocking
        data = await fetcher()

        # Only cache non-empty results to avoid caching transient errors
        if data:
            async with self._lock:
                self._trials_list = CacheEntry(
                    data=data,
                    expires_at=time.time() + self.TRIALS_LIST_TTL,
                )
            LOGGER.debug("Cache miss: trials_list, fetched %d trials", len(data))
        else:
            LOGGER.warning("Fetcher returned empty trials list, not caching")
            # Use stale cache if available (stale-while-revalidate pattern)
            async with self._lock:
                if self._trials_list is not None:
                    LOGGER.info("Using stale cache data due to empty fetch result")
                    return self._trials_list.data

        return data

    async def get_trial_info(
        self,
        trial_id: str,
        fetcher: Any,  # Callable that returns trial info dict
    ) -> dict[str, Any]:
        """Get cached trial info or fetch if expired.

        Uses longer TTL (1 hour) for completed games since they don't change.
        """
        async with self._lock:
            entry = self._trial_info.get(trial_id)
            if entry is not None and entry.is_valid():
                LOGGER.debug("Cache hit: trial_info[%s]", trial_id)
                return entry.data

        # Fetch outside lock
        data = await fetcher()

        # Use longer TTL for completed trials
        phase = data.get("phase", "unknown")
        is_completed = phase in ("completed", "stopped")
        ttl = 3600.0 if is_completed else self.TRIAL_INFO_TTL

        async with self._lock:
            self._trial_info[trial_id] = CacheEntry(
                data=data,
                expires_at=time.time() + ttl,
            )
        LOGGER.debug(
            "Cache miss: trial_info[%s] (completed=%s, ttl=%.0fs)",
            trial_id,
            is_completed,
            ttl,
        )
        return data

    async def get_trial_details(
        self,
        trial_id: str,
        trace_reader: "TraceReader",
    ) -> list[dict[str, Any]]:
        """Get cached trial details with incremental span fetching.

        This method implements an incremental caching strategy:
        1. On cache miss: Fetch all spans, serialize, and cache
        2. On cache hit: Fetch only new spans (since max_timestamp), merge with cached data

        Returns:
            List of serialized spans (items) ready for API response
        """
        from datetime import datetime, timezone

        # Initialize variables that will be used outside the lock
        entry: CacheEntry | None = None
        items: list[dict[str, Any]] = []
        max_timestamp: int = 0
        is_completed: bool = False
        start_time: datetime | None = None

        async with self._lock:
            entry = self._trial_details.get(trial_id)
            if entry is not None and entry.is_valid():
                # Cache hit
                cached_data = entry.data
                items = cached_data.get("items", [])
                max_timestamp = cached_data.get("max_timestamp", 0)
                is_completed = cached_data.get("is_completed", False)

                # If trial is completed, return cached data immediately (no new spans)
                if is_completed:
                    LOGGER.debug(
                        "Cache hit: trial_details[%s] (completed, no new spans)",
                        trial_id,
                    )
                    return items

                LOGGER.debug(
                    "Cache hit: trial_details[%s], fetching incremental spans since %d",
                    trial_id,
                    max_timestamp,
                )

                # Fetch new spans outside lock
                start_time = datetime.fromtimestamp(
                    max_timestamp / 1_000_000, tz=timezone.utc
                )

        # Fetch new spans (outside lock to avoid blocking)
        if entry is not None and entry.is_valid():
            new_spans = await trace_reader.get_spans(trial_id, start_time=start_time)

            # Serialize new spans
            new_items = []
            new_max_timestamp = max_timestamp
            is_now_completed = False
            for span in new_spans:
                typed = deserialize_span(span)
                if typed is not None:
                    new_items.append(serialize_span_for_ws(typed))
                    new_max_timestamp = max(new_max_timestamp, span.start_time)
                    # Check if trial just completed
                    if isinstance(typed, TrialLifecycleSpan) and typed.phase in (
                        "completed",
                        "stopped",
                    ):
                        is_now_completed = True

            if new_items:
                LOGGER.debug(
                    "Fetched %d new spans for trial %s, merging with cache",
                    len(new_items),
                    trial_id,
                )
                # Merge with cached items
                merged_items = items + new_items

                # Use longer TTL if trial just completed
                ttl = 3600.0 if is_now_completed else self.TRIAL_DETAILS_TTL

                # Update cache with merged data
                async with self._lock:
                    self._trial_details[trial_id] = CacheEntry(
                        data={
                            "items": merged_items,
                            "max_timestamp": new_max_timestamp,
                            "is_completed": is_now_completed,
                        },
                        expires_at=time.time() + ttl,
                    )

                return merged_items
            else:
                LOGGER.debug(
                    "No new spans for trial %s, returning cached data", trial_id
                )
                return items

        # Cache miss - fetch all spans
        LOGGER.debug("Cache miss: trial_details[%s], fetching all spans", trial_id)
        all_spans = await trace_reader.get_spans(trial_id)

        # Serialize all spans
        items = []
        max_timestamp = 0
        is_completed = False
        for span in all_spans:
            typed = deserialize_span(span)
            if typed is not None:
                items.append(serialize_span_for_ws(typed))
                max_timestamp = max(max_timestamp, span.start_time)
                # Check if trial is completed
                if isinstance(typed, TrialLifecycleSpan) and typed.phase in (
                    "completed",
                    "stopped",
                ):
                    is_completed = True

        # Use longer TTL for completed trials (1 hour) since they don't change
        ttl = 3600.0 if is_completed else self.TRIAL_DETAILS_TTL

        # Cache the result
        async with self._lock:
            self._trial_details[trial_id] = CacheEntry(
                data={
                    "items": items,
                    "max_timestamp": max_timestamp,
                    "is_completed": is_completed,
                },
                expires_at=time.time() + ttl,
            )

        LOGGER.debug(
            "Cached %d spans for trial %s with max_timestamp %d (completed=%s, ttl=%.0fs)",
            len(items),
            trial_id,
            max_timestamp,
            is_completed,
            ttl,
        )
        return items

    async def get_stats(
        self,
        fetcher: Any,
    ) -> StatsResponse:
        """Get cached stats or fetch if expired."""
        async with self._lock:
            if self._stats is not None and self._stats.is_valid():
                LOGGER.debug("Cache hit: stats")
                return self._stats.data

        data: StatsResponse = await fetcher()

        # Only cache non-empty results
        if data.games_played or data.live_now or data.wagered_today:
            async with self._lock:
                self._stats = CacheEntry(
                    data=data,
                    expires_at=time.time() + self.STATS_TTL,
                )
            LOGGER.debug("Cache miss: stats")
        else:
            LOGGER.warning("Fetcher returned empty stats, not caching")
            # Use stale cache if available
            async with self._lock:
                if self._stats is not None:
                    LOGGER.info("Using stale stats cache due to empty fetch result")
                    return self._stats.data

        return data

    async def get_leaderboard(
        self,
        fetcher: Any,
    ) -> list[LeaderboardEntry]:
        """Get cached leaderboard or fetch if expired."""
        async with self._lock:
            if self._leaderboard is not None and self._leaderboard.is_valid():
                LOGGER.debug("Cache hit: leaderboard")
                return self._leaderboard.data

        data = await fetcher()

        # Only cache non-empty results
        if data:
            async with self._lock:
                self._leaderboard = CacheEntry(
                    data=data,
                    expires_at=time.time() + self.LEADERBOARD_TTL,
                )
            LOGGER.debug("Cache miss: leaderboard")
        else:
            LOGGER.warning("Fetcher returned empty leaderboard, not caching")
            # Use stale cache if available
            async with self._lock:
                if self._leaderboard is not None:
                    LOGGER.info(
                        "Using stale leaderboard cache due to empty fetch result"
                    )
                    return self._leaderboard.data

        return data

    async def get_agent_actions(
        self,
        fetcher: Any,
    ) -> list["AgentActionResponse"]:
        """Get cached agent actions or fetch if expired."""
        async with self._lock:
            if self._agent_actions is not None and self._agent_actions.is_valid():
                LOGGER.debug("Cache hit: agent_actions")
                return self._agent_actions.data

        data = await fetcher()

        # Only cache non-empty results
        if data:
            async with self._lock:
                self._agent_actions = CacheEntry(
                    data=data,
                    expires_at=time.time() + self.AGENT_ACTIONS_TTL,
                )
            LOGGER.debug("Cache miss: agent_actions")
        else:
            LOGGER.warning("Fetcher returned empty agent actions, not caching")
            # Use stale cache if available
            async with self._lock:
                if self._agent_actions is not None:
                    LOGGER.info(
                        "Using stale agent_actions cache due to empty fetch result"
                    )
                    return self._agent_actions.data

        return data

    async def get_games(
        self,
        fetcher: Any,
    ) -> GamesResponse:
        """Get cached games list or fetch if expired."""
        async with self._lock:
            if self._games is not None and self._games.is_valid():
                LOGGER.debug("Cache hit: games")
                return self._games.data

        data: GamesResponse = await fetcher()

        # Only cache non-empty results
        has_data = data.live_games or data.upcoming_games or data.completed_games
        if has_data:
            async with self._lock:
                self._games = CacheEntry(
                    data=data,
                    expires_at=time.time() + self.GAMES_TTL,
                )
            LOGGER.debug("Cache miss: games")
        else:
            LOGGER.warning("Fetcher returned empty games data, not caching")
            # Use stale cache if available
            async with self._lock:
                if self._games is not None:
                    LOGGER.info("Using stale games cache due to empty fetch result")
                    return self._games.data

        return data

    def invalidate_trial(self, trial_id: str) -> None:
        """Invalidate cache for a specific trial."""
        if trial_id in self._trial_info:
            del self._trial_info[trial_id]
        if trial_id in self._trial_details:
            del self._trial_details[trial_id]
        # Also invalidate aggregated data since trial state changed
        self._stats = None
        self._games = None
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
        LOGGER.debug("Invalidated all cache entries")


@dataclass
class ArenaServerState:
    """Shared state for the Arena Server."""

    trace_reader: TraceReader
    broadcaster: SpanBroadcaster = field(default_factory=SpanBroadcaster)
    cache: LandingPageCache = field(default_factory=LandingPageCache)
    static_dir: Path | None = None
    poll_interval: float = 1.0  # Seconds between trace polls
    trace_backend: str = "jaeger"

    # Tracking last poll time per trial for incremental updates
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
                        "league": typed.league,
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
        cache: Optional cache for trial info (recommended for performance)
    """
    live_games: list[GameCardData] = []
    completed_games: list[GameCardData] = []

    for trial_id in trial_ids:
        try:
            # Use cache if available, otherwise fetch directly
            if cache is not None:
                trial_info = await cache.get_trial_info(
                    trial_id,
                    lambda: _extract_trial_info_from_traces(trace_reader, trial_id),
                )
            else:
                trial_info = await _extract_trial_info_from_traces(
                    trace_reader, trial_id
                )
        except Exception as e:
            LOGGER.warning("Failed to get info for trial '%s': %s", trial_id, e)
            continue

        phase = trial_info["phase"]
        metadata = trial_info["metadata"]
        league = metadata.get("league", "NBA")

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


def _format_relative_time(timestamp_us: int) -> str:
    """Format timestamp (microseconds) as relative time string.

    Args:
        timestamp_us: Timestamp in microseconds since epoch

    Returns:
        Relative time string like "2s ago", "5m ago", "1h ago"
    """
    now_us = time.time() * 1_000_000
    diff_us = now_us - timestamp_us
    diff_s = diff_us / 1_000_000

    if diff_s < 60:
        return f"{int(diff_s)}s ago"
    elif diff_s < 3600:
        return f"{int(diff_s / 60)}m ago"
    else:
        return f"{int(diff_s / 3600)}h ago"


def _format_agent_action_string(response: AgentResponseMessage) -> str:
    """Format AgentResponseMessage as human-readable action string.

    Args:
        response: The agent response message

    Returns:
        Formatted action string like:
        - '"BOS defense looking strong Q2"' (for content)
        - "placed $50 on LAL moneyline" (for bet)
        - "analyzing..." (fallback)
    """
    if response.content:
        # Quote the message
        return f'"{response.content}"'
    elif response.bet_amount and response.bet_amount > 0:
        # Format bet action
        bet_type = response.bet_type.lower() if response.bet_type else "bet"

        # Get selection (home/away/over/under)
        selection = response.bet_selection or "unknown"

        return f"placed ${int(response.bet_amount)} on {selection} {bet_type}"
    else:
        return "analyzing..."


def _format_agent_action(action: AgentAction) -> "AgentActionResponse":
    """Format AgentAction to AgentActionResponse for API response.

    Args:
        action: Raw AgentAction with response and timestamp

    Returns:
        AgentActionResponse with formatted action string and relative time
    """
    from dojozero.arena_server._models import AgentActionResponse

    return AgentActionResponse(
        agent=action.agent,
        action=_format_agent_action_string(action.response),
        time=_format_relative_time(action.timestamp),
    )


async def _extract_agent_actions(
    trace_reader: TraceReader,
    trial_ids: list[str],
    limit: int = 20,
) -> list["AgentActionResponse"]:
    """Extract recent agent actions from trial spans.

    Queries agent.response spans from recent games (both live and completed) and returns
    formatted AgentActionResponse objects with human-readable action strings and relative timestamps.

    Returns:
        List of formatted agent actions sorted by time (newest first)
    """
    all_actions: list[AgentAction] = []

    # Check recent trials for agent actions (both live and completed games)
    # Limit to 10 most recent trials to balance performance vs. content availability
    RECENT_TRIALS_LIMIT = 10
    for trial_id in trial_ids[:RECENT_TRIALS_LIMIT]:
        try:
            # Get agent.response spans from the entire trial
            # No time filter - we want recent actions from any games (live or completed)
            spans = await trace_reader.get_spans(
                trial_id,
                operation_names=["agent.response"],
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

    # Sort by timestamp (newest first), limit, and format
    all_actions.sort(key=lambda x: x.timestamp, reverse=True)
    formatted_actions = [_format_agent_action(action) for action in all_actions[:limit]]
    return formatted_actions


async def _compute_stats(
    trace_reader: TraceReader,
    trial_ids: list[str],
) -> StatsResponse:
    """Compute aggregate stats for landing page."""
    games_played = 0
    live_now = 0
    wagered_today = 0.0

    for trial_id in trial_ids:
        try:
            trial_info = await _extract_trial_info_from_traces(trace_reader, trial_id)
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


def create_arena_app(
    trace_backend: str,
    trace_query_endpoint: str | None = None,
    static_dir: Path | None = None,
    poll_interval: float = 1.0,
    service_name: str = "dojozero",
) -> FastAPI:
    """Create the Arena Server FastAPI application.

    Args:
        trace_backend: Trace backend type ("jaeger" or "sls")
        trace_query_endpoint: Jaeger Query API endpoint (only used when trace_backend="jaeger")
        static_dir: Path to static files (React build output)
        poll_interval: Interval for polling new spans
        service_name: Service name for Jaeger or SLS trace backend (use --service-name)

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
        _server_state = ArenaServerState(
            trace_reader=trace_reader,
            broadcaster=broadcaster,
            static_dir=static_dir,
            poll_interval=poll_interval,
            trace_backend=trace_backend,
        )
        LOGGER.info(
            "Arena Server started (trace backend: %s, static_dir: %s, service_name: %s)",
            trace_backend,
            static_dir,
            service_name,
        )
        yield
        # Cleanup
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

        return JSONResponse(content=[item.model_dump(by_alias=True) for item in result])

    @app.get("/api/trials/{trial_id}")
    async def get_trial(trial_id: str) -> JSONResponse:
        """Get trial info and spans with incremental caching.

        Uses a 60-second cache with incremental span fetching to reduce SLS load.
        On cache hit, only new spans (since last fetch) are retrieved and merged.
        """
        state = get_server_state()

        start_time = time.time()
        LOGGER.info("Fetching trial details for: %s", trial_id)

        # Use cached trial details with incremental fetching
        items = await state.cache.get_trial_details(trial_id, state.trace_reader)

        elapsed = time.time() - start_time
        LOGGER.info(
            "Trial %s: Returned %d items in %.2fs",
            trial_id,
            len(items) if items else 0,
            elapsed,
        )

        if not items:
            # Check if trial exists (may have no spans yet)
            trial_ids = await state.trace_reader.list_trials()
            if trial_id not in trial_ids:
                return JSONResponse(
                    content={"error": f"Trial '{trial_id}' not found"},
                    status_code=404,
                )

        response = TrialDetailResponse(trial_id=trial_id, items=items)
        return JSONResponse(content=response.model_dump(by_alias=True))

    # -------------------------------------------------------------------------
    # Landing Page Endpoints (with caching)
    # -------------------------------------------------------------------------

    @app.get("/api/landing")
    async def get_landing_data(
        days: int = Query(
            default=7,
            description="Number of days to look back for games.",
            ge=1,
            le=30,
        ),
    ) -> JSONResponse:
        """Get aggregated landing page data.

        Returns games, stats, and recent agent actions in a single call.
        Data is cached to reduce load on the trace store.
        """
        state = get_server_state()

        # Fetch trial IDs (cached)
        async def fetch_trials() -> list[str]:
            start_dt = datetime.now(timezone.utc) - timedelta(days=days)
            return await state.trace_reader.list_trials(start_time=start_dt, limit=100)

        trial_ids = await state.cache.get_trials_list(fetch_trials)

        # Fetch stats (cached)
        async def fetch_stats() -> StatsResponse:
            return await _compute_stats(state.trace_reader, trial_ids)

        stats = await state.cache.get_stats(fetch_stats)

        # Fetch games (cached)
        async def fetch_games() -> GamesResponse:
            return await _extract_games_from_trials(
                state.trace_reader, trial_ids, state.cache
            )

        games = await state.cache.get_games(fetch_games)

        # Fetch agent actions (cached, short TTL)
        async def fetch_actions() -> list["AgentActionResponse"]:
            return await _extract_agent_actions(state.trace_reader, trial_ids, limit=12)

        agent_actions = await state.cache.get_agent_actions(fetch_actions)

        all_games = games.live_games + games.upcoming_games + games.completed_games
        response = LandingResponse(
            stats=stats,
            live_games=games.live_games,
            all_games=all_games,
            live_agent_actions=agent_actions,
        )
        return JSONResponse(content=response.model_dump(by_alias=True))

    @app.get("/api/stats")
    async def get_stats(
        days: int = Query(
            default=7,
            description="Number of days to aggregate stats over.",
            ge=1,
            le=30,
        ),
    ) -> JSONResponse:
        """Get real-time stats for the hero section.

        Returns:
            gamesPlayed: Total completed games
            liveNow: Currently running games
            wageredToday: Total amount wagered (if available)
        """
        state = get_server_state()

        async def fetch_trials() -> list[str]:
            start_dt = datetime.now(timezone.utc) - timedelta(days=days)
            return await state.trace_reader.list_trials(start_time=start_dt, limit=100)

        trial_ids = await state.cache.get_trials_list(fetch_trials)

        async def fetch_stats() -> StatsResponse:
            return await _compute_stats(state.trace_reader, trial_ids)

        stats = await state.cache.get_stats(fetch_stats)
        return JSONResponse(content=stats.model_dump(by_alias=True))

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
        days: int = Query(
            default=7,
            description="Number of days to look back.",
            ge=1,
            le=30,
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
        """
        state = get_server_state()

        async def fetch_trials() -> list[str]:
            start_dt = datetime.now(timezone.utc) - timedelta(days=days)
            return await state.trace_reader.list_trials(
                start_time=start_dt, limit=limit
            )

        trial_ids = await state.cache.get_trials_list(fetch_trials)

        async def fetch_games() -> GamesResponse:
            return await _extract_games_from_trials(
                state.trace_reader, trial_ids, state.cache
            )

        games_data = await state.cache.get_games(fetch_games)

        # Apply filters
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

        if league:
            all_games = [g for g in all_games if g.league.upper() == league.upper()]

        filtered = all_games[:limit]
        return JSONResponse(
            content={
                "games": [g.model_dump(by_alias=True) for g in filtered],
                "total": len(all_games),
            }
        )

    @app.get("/api/leaderboard")
    async def get_leaderboard(
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc.",
        ),
        days: int = Query(
            default=30,
            description="Number of days to aggregate over.",
            ge=1,
            le=365,
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
        """
        state = get_server_state()

        async def fetch_trials() -> list[str]:
            start_dt = datetime.now(timezone.utc) - timedelta(days=days)
            return await state.trace_reader.list_trials(start_time=start_dt, limit=500)

        trial_ids = await state.cache.get_trials_list(fetch_trials)

        async def fetch_leaderboard() -> list[LeaderboardEntry]:
            return await _compute_leaderboard(state.trace_reader, trial_ids, limit)

        leaderboard = await state.cache.get_leaderboard(fetch_leaderboard)

        # Filter by league if specified (would need to track in agent stats)
        # For now, return all agents

        response = LeaderboardResponse(leaderboard=leaderboard)
        return JSONResponse(
            content=response.model_dump(by_alias=True),
        )

    @app.get("/api/agent-actions")
    async def get_agent_actions(
        limit: int = Query(
            default=20,
            description="Maximum number of actions to return.",
            ge=1,
            le=100,
        ),
    ) -> JSONResponse:
        """Get recent agent actions for the live ticker.

        Returns the most recent agent actions sorted by time.
        """
        state = get_server_state()

        async def fetch_trials() -> list[str]:
            start_dt = datetime.now(timezone.utc) - timedelta(hours=1)
            return await state.trace_reader.list_trials(start_time=start_dt, limit=20)

        trial_ids = await state.cache.get_trials_list(fetch_trials)

        async def fetch_actions() -> list["AgentActionResponse"]:
            return await _extract_agent_actions(state.trace_reader, trial_ids, limit)

        actions = await state.cache.get_agent_actions(fetch_actions)

        response = AgentActionsResponse(actions=actions)
        return JSONResponse(
            content=response.model_dump(by_alias=True),
        )

    # -------------------------------------------------------------------------
    # WebSocket Endpoint for Real-time Streaming
    # -------------------------------------------------------------------------

    @app.websocket("/ws/test/replay")
    async def test_replay_stream(websocket: WebSocket):
        """WebSocket endpoint for testing with recorded replay data.

        This endpoint replays the bundled snapshot_data.json file, allowing
        frontend developers to test and debug without needing a live trial.

        Control commands (send as JSON):
            {"command": "speed", "value": 2}  - Set playback speed (0.1x to 10x)
            {"command": "pause"}              - Pause playback
            {"command": "resume"}             - Resume playback
            {"command": "reset"}              - Reset to beginning
            {"command": "skip", "value": 10}  - Skip forward N events
            {"command": "seek", "value": 50}  - Jump to event index N
            {"command": "status"}             - Get current playback status
        """
        handler = create_replay_websocket_handler()
        await handler(websocket)

    @app.websocket("/ws/trials/{trial_id}/stream")
    async def trial_stream(websocket: WebSocket, trial_id: str):
        """WebSocket endpoint for real-time span streaming.

        Protocol:
        - Server sends 'snapshot' immediately upon connection
        - Server pushes 'span' messages as new spans are detected
        - Server sends 'trial_ended' when trial completes
        - Server sends 'heartbeat' periodically
        """
        state = get_server_state()
        await websocket.accept()
        LOGGER.info("WebSocket connection accepted for trial '%s'", trial_id)

        try:
            await state.broadcaster.subscribe(trial_id, websocket)

            # Send initial snapshot
            spans = await state.trace_reader.get_spans(trial_id)
            await state.broadcaster.send_snapshot(trial_id, websocket, spans)

            # Track seen span IDs to avoid duplicates
            seen_span_ids: set[str] = {s.span_id for s in spans}

            # Track last seen timestamp for efficient querying
            last_time = datetime.now(timezone.utc)
            if spans:
                # Get the latest span timestamp
                last_us = max(s.start_time for s in spans)
                last_time = datetime.fromtimestamp(last_us / 1_000_000, tz=timezone.utc)

            # Poll for new spans and broadcast
            while True:
                try:
                    # Wait for either a client message or timeout
                    await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=state.poll_interval,
                    )
                except asyncio.TimeoutError:
                    # Poll for new spans (start_time for incremental updates)
                    new_spans = await state.trace_reader.get_spans(
                        trial_id, start_time=last_time
                    )

                    # Filter out already-seen spans (double protection)
                    truly_new_spans = [
                        s for s in new_spans if s.span_id not in seen_span_ids
                    ]

                    # Broadcast only new spans
                    for span in truly_new_spans:
                        await state.broadcaster.broadcast_span(trial_id, span)
                        seen_span_ids.add(span.span_id)

                    if truly_new_spans:
                        last_us = max(s.start_time for s in truly_new_spans)
                        last_time = datetime.fromtimestamp(
                            last_us / 1_000_000, tz=timezone.utc
                        )
                        LOGGER.debug(
                            "Sent %d new spans for trial '%s'",
                            len(truly_new_spans),
                            trial_id,
                        )

                    # Send heartbeat
                    heartbeat = WSHeartbeatMessage(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                    await websocket.send_text(heartbeat.model_dump_json())

        except WebSocketDisconnect:
            LOGGER.info("WebSocket disconnected for trial '%s'", trial_id)
        except Exception as e:
            LOGGER.error("WebSocket error for trial '%s': %s", trial_id, e)
        finally:
            await state.broadcaster.unsubscribe(trial_id, websocket)

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

    # -------------------------------------------------------------------------
    # Test/Replay Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/test/replay-info")
    async def get_replay_info() -> JSONResponse:
        """Get information about the available replay data.

        Returns metadata about the bundled snapshot_data.json file,
        including total items, categories breakdown, and trial info.
        """
        from collections import Counter

        from dojozero.arena_server._replay import DEFAULT_SNAPSHOT_PATH

        if not DEFAULT_SNAPSHOT_PATH.exists():
            return JSONResponse(
                content={"error": "No replay data available"},
                status_code=404,
            )

        import json

        with open(DEFAULT_SNAPSHOT_PATH) as f:
            data = json.load(f)

        items = data.get("items", [])
        categories = Counter(item.get("category", "unknown") for item in items)

        # Extract basic trial info from first items
        trial_info = {}
        for item in items[:20]:
            if item.get("category") == "event.game_initialize":
                game_data = item.get("data", {})
                trial_info = {
                    "game_id": game_data.get("game_id"),
                    "sport": game_data.get("sport"),
                    "home_team": game_data.get("home_team", {}).get("name"),
                    "away_team": game_data.get("away_team", {}).get("name"),
                }
                break

        return JSONResponse(
            content={
                "total_items": len(items),
                "categories": dict(categories.most_common()),
                "trial_info": trial_info,
                "websocket_url": "/ws/test/replay",
                "commands": [
                    {
                        "command": "speed",
                        "value": "number",
                        "description": "Set playback speed (0.1x to 10x)",
                    },
                    {"command": "pause", "description": "Pause playback"},
                    {"command": "resume", "description": "Resume playback"},
                    {"command": "reset", "description": "Reset to beginning"},
                    {
                        "command": "skip",
                        "value": "number",
                        "description": "Skip forward N events",
                    },
                    {
                        "command": "seek",
                        "value": "number",
                        "description": "Jump to event index N",
                    },
                    {"command": "status", "description": "Get current playback status"},
                ],
            }
        )

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
) -> None:
    """Run the Arena Server.

    Args:
        host: Host to bind to
        port: Port to listen on
        trace_backend: Trace backend type ("jaeger" or "sls")
        trace_query_endpoint: Jaeger Query API endpoint (only used when trace_backend="jaeger")
        static_dir: Path to static files (React build output)
        service_name: Service name for Jaeger or SLS trace backend (use --service-name)

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
    "CacheEntry",
    "LandingPageCache",
    "SpanBroadcaster",
    "WSMessageType",
    "create_arena_app",
    "create_trace_reader",
    "get_server_state",
    "run_arena_server",
]
