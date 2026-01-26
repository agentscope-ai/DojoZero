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
import json
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

from dojozero.core._tracing import (
    SpanData,
    TraceReader,
    create_trace_reader,
)

# NBA team data lookup: tricode -> {name, city, color}
# Used to fill in team details when not available in trial metadata
_NBA_TEAMS: dict[str, dict[str, str]] = {
    "ATL": {"name": "Hawks", "city": "Atlanta", "color": "#E03A3E"},
    "BOS": {"name": "Celtics", "city": "Boston", "color": "#007A33"},
    "BKN": {"name": "Nets", "city": "Brooklyn", "color": "#000000"},
    "CHA": {"name": "Hornets", "city": "Charlotte", "color": "#1D1160"},
    "CHI": {"name": "Bulls", "city": "Chicago", "color": "#CE1141"},
    "CLE": {"name": "Cavaliers", "city": "Cleveland", "color": "#860038"},
    "DAL": {"name": "Mavericks", "city": "Dallas", "color": "#00538C"},
    "DEN": {"name": "Nuggets", "city": "Denver", "color": "#0E2240"},
    "DET": {"name": "Pistons", "city": "Detroit", "color": "#C8102E"},
    "GSW": {"name": "Warriors", "city": "Golden State", "color": "#1D428A"},
    "HOU": {"name": "Rockets", "city": "Houston", "color": "#CE1141"},
    "IND": {"name": "Pacers", "city": "Indiana", "color": "#002D62"},
    "LAC": {"name": "Clippers", "city": "Los Angeles", "color": "#C8102E"},
    "LAL": {"name": "Lakers", "city": "Los Angeles", "color": "#552583"},
    "MEM": {"name": "Grizzlies", "city": "Memphis", "color": "#5D76A9"},
    "MIA": {"name": "Heat", "city": "Miami", "color": "#98002E"},
    "MIL": {"name": "Bucks", "city": "Milwaukee", "color": "#00471B"},
    "MIN": {"name": "Timberwolves", "city": "Minnesota", "color": "#0C2340"},
    "NOP": {"name": "Pelicans", "city": "New Orleans", "color": "#0C2340"},
    "NYK": {"name": "Knicks", "city": "New York", "color": "#F58426"},
    "OKC": {"name": "Thunder", "city": "Oklahoma City", "color": "#007AC1"},
    "ORL": {"name": "Magic", "city": "Orlando", "color": "#0077C0"},
    "PHI": {"name": "76ers", "city": "Philadelphia", "color": "#006BB6"},
    "PHX": {"name": "Suns", "city": "Phoenix", "color": "#1D1160"},
    "POR": {"name": "Trail Blazers", "city": "Portland", "color": "#E03A3E"},
    "SAC": {"name": "Kings", "city": "Sacramento", "color": "#5A2D81"},
    "SAS": {"name": "Spurs", "city": "San Antonio", "color": "#C4CED4"},
    "TOR": {"name": "Raptors", "city": "Toronto", "color": "#CE1141"},
    "UTA": {"name": "Jazz", "city": "Utah", "color": "#002B5C"},
    "WAS": {"name": "Wizards", "city": "Washington", "color": "#002B5C"},
}

# NFL team data lookup (for future NFL support)
_NFL_TEAMS: dict[str, dict[str, str]] = {
    "KC": {"name": "Chiefs", "city": "Kansas City", "color": "#E31837"},
    "SF": {"name": "49ers", "city": "San Francisco", "color": "#AA0000"},
    "BUF": {"name": "Bills", "city": "Buffalo", "color": "#00338D"},
    "PHI": {"name": "Eagles", "city": "Philadelphia", "color": "#004C54"},
    "DAL": {"name": "Cowboys", "city": "Dallas", "color": "#003594"},
    "GB": {"name": "Packers", "city": "Green Bay", "color": "#203731"},
}


def _get_team_info(tricode: str, league: str = "NBA") -> dict[str, str]:
    """Get team info by tricode.

    Returns dict with name, city, color. Falls back to defaults if not found.
    """
    teams = _NBA_TEAMS if league == "NBA" else _NFL_TEAMS
    if tricode in teams:
        return {**teams[tricode], "abbrev": tricode}
    # Fallback for unknown teams
    return {"name": tricode, "city": "", "color": "#666666", "abbrev": tricode}


# Agent color palette for visual distinction
_AGENT_COLORS = [
    "#3B82F6",  # Blue
    "#8B5CF6",  # Purple
    "#10B981",  # Green
    "#F59E0B",  # Amber
    "#EF4444",  # Red
    "#EC4899",  # Pink
    "#14B8A6",  # Teal
    "#6366F1",  # Indigo
]


def _get_agent_info(agent_id: str, agent_name: str | None = None) -> dict[str, Any]:
    """Get agent info with consistent display fields.

    Returns dict with id, name, avatar, color, model for frontend display.
    """
    name = agent_name or agent_id
    # Generate consistent color based on agent_id hash
    color_idx = hash(agent_id) % len(_AGENT_COLORS)
    avatar = name[0].upper() if name else "?"

    return {
        "id": agent_id,
        "name": name,
        "avatar": avatar,
        "color": _AGENT_COLORS[color_idx],
        "model": "AI Agent",  # Default model name
    }


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
        """Broadcast a span to all clients subscribed to a trial."""
        message = {
            "type": WSMessageType.SPAN,
            "trial_id": trial_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": span.to_dict(),
        }
        await self._send_to_trial(trial_id, message)

    async def broadcast_trial_ended(self, trial_id: str) -> None:
        """Notify all clients that a trial has ended."""
        message = {
            "type": WSMessageType.TRIAL_ENDED,
            "trial_id": trial_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._send_to_trial(trial_id, message)

    async def send_snapshot(
        self,
        trial_id: str,
        websocket: WebSocket,
        spans: list[SpanData],
    ) -> None:
        """Send a snapshot of recent spans to a specific client."""
        message = {
            "type": WSMessageType.SNAPSHOT,
            "trial_id": trial_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "spans": [span.to_dict() for span in spans],
            },
        }
        await self._send_to_client(websocket, message)

    async def _send_to_trial(self, trial_id: str, message: dict[str, Any]) -> None:
        """Send a message to all clients subscribed to a trial."""
        async with self._lock:
            clients = list(self._clients.get(trial_id, set()))

        if not clients:
            return

        text = json.dumps(message, default=str)
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
        message: dict[str, Any],
    ) -> None:
        """Send a message to a specific client."""
        try:
            text = json.dumps(message, default=str)
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
    TRIALS_LIST_TTL: float = 30.0
    TRIAL_INFO_TTL: float = 10.0
    STATS_TTL: float = 5.0
    LEADERBOARD_TTL: float = 30.0
    AGENT_ACTIONS_TTL: float = 2.0
    GAMES_TTL: float = 10.0

    _trials_list: CacheEntry | None = None
    _trial_info: dict[str, CacheEntry] = field(default_factory=dict)
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
        """Get cached trial info or fetch if expired."""
        async with self._lock:
            entry = self._trial_info.get(trial_id)
            if entry is not None and entry.is_valid():
                LOGGER.debug("Cache hit: trial_info[%s]", trial_id)
                return entry.data

        # Fetch outside lock
        data = await fetcher()

        async with self._lock:
            self._trial_info[trial_id] = CacheEntry(
                data=data,
                expires_at=time.time() + self.TRIAL_INFO_TTL,
            )
        LOGGER.debug("Cache miss: trial_info[%s]", trial_id)
        return data

    async def get_stats(
        self,
        fetcher: Any,
    ) -> dict[str, Any]:
        """Get cached stats or fetch if expired."""
        async with self._lock:
            if self._stats is not None and self._stats.is_valid():
                LOGGER.debug("Cache hit: stats")
                return self._stats.data

        data = await fetcher()

        # Only cache non-empty results
        if data and any(data.values()):
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
    ) -> list[dict[str, Any]]:
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
    ) -> list[dict[str, Any]]:
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
    ) -> dict[str, Any]:
        """Get cached games list or fetch if expired."""
        async with self._lock:
            if self._games is not None and self._games.is_valid():
                LOGGER.debug("Cache hit: games")
                return self._games.data

        data = await fetcher()

        # Only cache non-empty results (check if any game lists have data)
        has_data = data and any(
            data.get(key) for key in ["liveGames", "upcomingGames", "completedGames"]
        )
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
        # Also invalidate aggregated data since trial state changed
        self._stats = None
        self._games = None
        LOGGER.debug("Invalidated cache for trial: %s", trial_id)

    def invalidate_all(self) -> None:
        """Invalidate all cached data."""
        self._trials_list = None
        self._trial_info.clear()
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

    Returns:
        dict with "phase" and "metadata" extracted from spans
    """
    try:
        spans = await trace_reader.get_spans(trial_id)
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

    for span in spans:
        op_name = span.operation_name
        tags = span.tags

        # Check lifecycle spans and extract metadata
        if op_name == "trial.started":
            has_started = True
            if span.start_time > latest_start_time:
                latest_start_time = span.start_time

            # Extract metadata from trial.* tags (excluding system tags)
            for key, value in tags.items():
                if key.startswith("trial.") and key not in ("trial.phase",):
                    # Convert trial.home_team_tricode -> home_team_tricode
                    metadata_key = key[6:]  # Remove "trial." prefix
                    metadata[metadata_key] = value

        elif op_name in ("trial.stopped", "trial.terminated"):
            has_stopped = True
            if span.start_time > latest_stop_time:
                latest_stop_time = span.start_time

        # Check for game completion spans (NBA/NFL game results)
        elif op_name in ("game_result", "nfl_game_result") or "game_result" in op_name:
            has_game_result = True

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

    return {"phase": phase, "metadata": metadata}


async def _extract_games_from_trials(
    trace_reader: TraceReader,
    trial_ids: list[str],
) -> dict[str, Any]:
    """Extract games list from trials for landing page.

    Returns:
        dict with "live", "upcoming", "completed" game lists
    """
    live_games: list[dict[str, Any]] = []
    completed_games: list[dict[str, Any]] = []

    for trial_id in trial_ids:
        try:
            trial_info = await _extract_trial_info_from_traces(trace_reader, trial_id)
        except Exception as e:
            LOGGER.warning("Failed to get info for trial '%s': %s", trial_id, e)
            continue

        phase = trial_info["phase"]
        metadata = trial_info["metadata"]
        league = metadata.get("league", "NBA")

        # Extract team tricodes from metadata (default to "TBD" if not present)
        home_tricode = metadata.get("home_team_tricode", "TBD")
        away_tricode = metadata.get("away_team_tricode", "TBD")

        # Get full team info (with fallback lookup from static team data)
        home_team_info = _get_team_info(home_tricode, league)
        away_team_info = _get_team_info(away_tricode, league)

        # Override with metadata team names if provided
        if metadata.get("home_team_name"):
            home_team_info["name"] = metadata["home_team_name"]
        if metadata.get("away_team_name"):
            away_team_info["name"] = metadata["away_team_name"]

        # Extract game data from trial metadata
        game_data: dict[str, Any] = {
            "id": trial_id,
            "league": league,
            "homeTeam": home_team_info,
            "awayTeam": away_team_info,
            "homeScore": metadata.get("home_score", 0),
            "awayScore": metadata.get("away_score", 0),
            "status": phase,
            "date": metadata.get("game_date", ""),
        }

        if phase == "running":
            game_data["quarter"] = metadata.get("quarter", "")
            game_data["clock"] = metadata.get("clock", "")
            game_data["bets"] = []  # Will be populated from spans
            live_games.append(game_data)
        elif phase == "completed":
            # Extract winner info from game result spans
            game_data["winner"] = metadata.get("winner_agent", None)
            game_data["winAmount"] = metadata.get("win_amount", 0)
            completed_games.append(game_data)

    return {
        "liveGames": live_games,
        "upcomingGames": [],  # Would need to query from a different source
        "completedGames": completed_games,
    }


async def _extract_agent_actions(
    trace_reader: TraceReader,
    trial_ids: list[str],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Extract recent agent actions from trial spans.

    Looks for spans like "agent.action", "bet.placed", "agent.thinking" etc.

    Returns:
        List of recent agent actions sorted by time (newest first)
    """
    all_actions: list[dict[str, Any]] = []

    # Limit to checking the 10 most recent trials for live actions to improve performance.
    RECENT_TRIALS_LIMIT = 10
    for trial_id in trial_ids[:RECENT_TRIALS_LIMIT]:
        try:
            # Get recent spans only
            start_time = datetime.now(timezone.utc) - timedelta(minutes=5)
            spans = await trace_reader.get_spans(trial_id, start_time=start_time)
        except Exception as e:
            LOGGER.warning("Failed to get spans for trial '%s': %s", trial_id, e)
            continue

        for span in spans:
            op_name = span.operation_name

            # Look for action-related spans
            if any(
                keyword in op_name
                for keyword in ["bet.placed", "agent.action", "agent.thinking", "bet."]
            ):
                agent_id = span.tags.get("agent.id", span.tags.get("agent_id", ""))
                agent_name = span.tags.get(
                    "agent.name", span.tags.get("agent_name", agent_id)
                )

                action_text = span.tags.get(
                    "action.description",
                    span.tags.get("description", op_name),
                )

                # Calculate time ago
                span_time = datetime.fromtimestamp(
                    span.start_time / 1_000_000, tz=timezone.utc
                )
                seconds_ago = (datetime.now(timezone.utc) - span_time).total_seconds()

                if seconds_ago < 60:
                    time_ago = f"{int(seconds_ago)}s ago"
                elif seconds_ago < 3600:
                    time_ago = f"{int(seconds_ago // 60)}m ago"
                else:
                    time_ago = f"{int(seconds_ago // 3600)}h ago"

                all_actions.append(
                    {
                        "id": span.span_id,
                        "agent": _get_agent_info(agent_id, agent_name),
                        "action": action_text,
                        "time": time_ago,
                        "timestamp": span.start_time,
                    }
                )

    # Sort by timestamp (newest first) and limit
    all_actions.sort(key=lambda x: x["timestamp"], reverse=True)
    return all_actions[:limit]


async def _compute_stats(
    trace_reader: TraceReader,
    trial_ids: list[str],
) -> dict[str, Any]:
    """Compute aggregate stats for landing page.

    Returns:
        dict with gamesPlayed, liveNow, wageredToday
    """
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

    return {
        "gamesPlayed": games_played,
        "liveNow": live_now,
        "wageredToday": int(wagered_today),
    }


async def _compute_leaderboard(
    trace_reader: TraceReader,
    trial_ids: list[str],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Compute agent leaderboard from trial results.

    Returns:
        List of agents sorted by winnings (highest first)
    """
    agent_stats: dict[str, dict[str, Any]] = {}

    for trial_id in trial_ids:
        try:
            spans = await trace_reader.get_spans(trial_id)
        except Exception:
            continue

        for span in spans:
            op_name = span.operation_name
            tags = span.tags

            # Look for result spans
            if "result" in op_name or "payout" in op_name:
                agent_id = tags.get("agent.id", tags.get("agent_id", ""))
                if not agent_id:
                    continue

                payout = float(tags.get("payout", tags.get("profit", 0)))
                wager = float(tags.get("wager", tags.get("amount", 0)))
                won = tags.get("won", tags.get("result", "")) in ("win", "won", True)

                if agent_id not in agent_stats:
                    agent_name = tags.get("agent.name", agent_id)
                    agent_stats[agent_id] = {
                        "agent": _get_agent_info(agent_id, agent_name),
                        "winnings": 0.0,
                        "wins": 0,
                        "totalBets": 0,
                        "totalWagered": 0.0,
                    }

                agent_stats[agent_id]["winnings"] += payout
                agent_stats[agent_id]["totalBets"] += 1
                agent_stats[agent_id]["totalWagered"] += wager
                if won:
                    agent_stats[agent_id]["wins"] += 1

    # Convert to sorted list
    leaderboard: list[dict[str, Any]] = []
    for agent_id, stats in agent_stats.items():
        total_bets = stats["totalBets"]
        win_rate = (stats["wins"] / total_bets * 100) if total_bets > 0 else 0
        roi = (
            (stats["winnings"] / stats["totalWagered"] * 100)
            if stats["totalWagered"] > 0
            else 0
        )

        leaderboard.append(
            {
                "agent": stats["agent"],
                "winnings": round(stats["winnings"], 2),
                "winRate": round(win_rate, 1),
                "totalBets": total_bets,
                "roi": round(roi, 1),
            }
        )

    # Sort by winnings (descending) and add rank
    leaderboard.sort(key=lambda x: x["winnings"], reverse=True)
    for i, entry in enumerate(leaderboard[:limit]):
        entry["rank"] = i + 1

    return leaderboard[:limit]


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
        result = []
        for tid in trial_ids:
            trial_info_extracted = await _extract_trial_info_from_traces(
                state.trace_reader, tid
            )
            trial_info = {
                "id": tid,
                "phase": trial_info_extracted["phase"],
                "metadata": trial_info_extracted["metadata"],
            }
            result.append(trial_info)

        return JSONResponse(content=result)

    @app.get("/api/trials/{trial_id}")
    async def get_trial(trial_id: str) -> JSONResponse:
        """Get trial info and spans."""
        state = get_server_state()
        spans = await state.trace_reader.get_spans(trial_id)

        if not spans:
            # Check if trial exists (may have no spans yet)
            trial_ids = await state.trace_reader.list_trials()
            if trial_id not in trial_ids:
                return JSONResponse(
                    content={"error": f"Trial '{trial_id}' not found"},
                    status_code=404,
                )

        return JSONResponse(
            content={
                "trial_id": trial_id,
                "spans": [span.to_dict() for span in spans],
            }
        )

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
        async def fetch_stats() -> dict[str, Any]:
            return await _compute_stats(state.trace_reader, trial_ids)

        stats = await state.cache.get_stats(fetch_stats)

        # Fetch games (cached)
        async def fetch_games() -> dict[str, Any]:
            return await _extract_games_from_trials(state.trace_reader, trial_ids)

        games = await state.cache.get_games(fetch_games)

        # Fetch agent actions (cached, short TTL)
        async def fetch_actions() -> list[dict[str, Any]]:
            return await _extract_agent_actions(state.trace_reader, trial_ids, limit=12)

        agent_actions = await state.cache.get_agent_actions(fetch_actions)

        return JSONResponse(
            content={
                "stats": stats,
                "liveGames": games["liveGames"],
                "allGames": (
                    games["liveGames"]
                    + games["upcomingGames"]
                    + games["completedGames"]
                ),
                "liveAgentActions": agent_actions,
            }
        )

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

        async def fetch_stats() -> dict[str, Any]:
            return await _compute_stats(state.trace_reader, trial_ids)

        stats = await state.cache.get_stats(fetch_stats)
        return JSONResponse(content=stats)

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

        async def fetch_games() -> dict[str, Any]:
            return await _extract_games_from_trials(state.trace_reader, trial_ids)

        games_data = await state.cache.get_games(fetch_games)

        # Apply filters
        all_games = (
            games_data["liveGames"]
            + games_data["upcomingGames"]
            + games_data["completedGames"]
        )

        if status:
            status_map = {
                "live": "running",
                "upcoming": "upcoming",
                "completed": "completed",
            }
            target_status = status_map.get(status, status)
            all_games = [g for g in all_games if g.get("status") == target_status]

        if league:
            all_games = [
                g for g in all_games if g.get("league", "").upper() == league.upper()
            ]

        return JSONResponse(
            content={
                "games": all_games[:limit],
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

        async def fetch_leaderboard() -> list[dict[str, Any]]:
            return await _compute_leaderboard(state.trace_reader, trial_ids, limit)

        leaderboard = await state.cache.get_leaderboard(fetch_leaderboard)

        # Filter by league if specified (would need to track in agent stats)
        # For now, return all agents

        return JSONResponse(content={"leaderboard": leaderboard})

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

        async def fetch_actions() -> list[dict[str, Any]]:
            return await _extract_agent_actions(state.trace_reader, trial_ids, limit)

        actions = await state.cache.get_agent_actions(fetch_actions)

        return JSONResponse(content={"actions": actions})

    # -------------------------------------------------------------------------
    # WebSocket Endpoint for Real-time Streaming
    # -------------------------------------------------------------------------

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
                    heartbeat = {
                        "type": WSMessageType.HEARTBEAT,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    await websocket.send_text(json.dumps(heartbeat))

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
