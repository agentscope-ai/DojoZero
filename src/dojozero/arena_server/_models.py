"""Typed Pydantic models for arena server API responses and WebSocket messages.

These are presentation-layer view models assembled by the arena server from
deserialized spans, static lookups, and computed aggregations.  They are
**not** SLS span types — they are cross-trial, frontend-facing contracts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dojozero.betting._models import AgentInfo
from dojozero.core._models import AgentAction, LeaderboardEntry
from dojozero.data._models import TeamIdentity

# ============================================================================
# Arena API Response Models
# ============================================================================


class StatsResponse(BaseModel):
    """Hero section stats for /api/stats and /api/landing."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    games_played: int = Field(default=0, serialization_alias="gamesPlayed")
    live_now: int = Field(default=0, serialization_alias="liveNow")
    wagered_today: int = Field(default=0, serialization_alias="wageredToday")
    total_agents: int = Field(default=0, serialization_alias="totalAgents")


class GameCardData(BaseModel):
    """Single game card for landing/games pages."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str
    league: str = ""
    home_team: TeamIdentity = Field(
        default_factory=TeamIdentity,
        serialization_alias="homeTeam",
    )
    away_team: TeamIdentity = Field(
        default_factory=TeamIdentity,
        serialization_alias="awayTeam",
    )
    home_score: int = Field(default=0, serialization_alias="homeScore")
    away_score: int = Field(default=0, serialization_alias="awayScore")
    status: str = ""
    date: str = ""
    # Live-only fields
    quarter: str = ""
    clock: str = ""
    bets: list["BetSummary"] = Field(default_factory=list)
    # Completed-only fields
    winner: str | None = None
    win_amount: float = Field(default=0, serialization_alias="winAmount")


class GamesResponse(BaseModel):
    """Response for /api/games and internal games extraction."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    live_games: list[GameCardData] = Field(
        default_factory=list, serialization_alias="liveGames"
    )
    upcoming_games: list[GameCardData] = Field(
        default_factory=list, serialization_alias="upcomingGames"
    )
    completed_games: list[GameCardData] = Field(
        default_factory=list, serialization_alias="completedGames"
    )


class TrialListItem(BaseModel):
    """Single item in /api/trials response."""

    model_config = ConfigDict(frozen=True)

    id: str
    phase: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrialDetailResponse(BaseModel):
    """Response for /api/trials/{trial_id}."""

    model_config = ConfigDict(frozen=True)

    trial_id: str
    items: list[dict[str, Any]] = Field(default_factory=list)


class LandingResponse(BaseModel):
    """Response for /api/landing."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    stats: StatsResponse
    live_games: list[GameCardData] = Field(
        default_factory=list, serialization_alias="liveGames"
    )
    all_games: list[GameCardData] = Field(
        default_factory=list, serialization_alias="allGames"
    )
    live_agent_actions: list[AgentAction] = Field(
        default_factory=list, serialization_alias="liveAgentActions"
    )


class LeaderboardResponse(BaseModel):
    """Response for /api/leaderboard."""

    model_config = ConfigDict(frozen=True)

    leaderboard: list[LeaderboardEntry] = Field(default_factory=list)


class BetSummary(BaseModel):
    """Summary of a single bet for game card display."""

    model_config = ConfigDict(frozen=True)

    agent: "AgentInfo"  # From betting/_models.py
    team: str  # Team tricode (e.g., "LAL")
    amount: float
    type: str  # "moneyline", "spread", "total", etc.


class AgentActionsResponse(BaseModel):
    """Response for /api/agent-actions."""

    model_config = ConfigDict(frozen=True)

    actions: list[AgentAction] = Field(default_factory=list)


# ============================================================================
# WebSocket Message Models
# ============================================================================


class WSSpanMessage(BaseModel):
    """WebSocket message wrapping a single span."""

    model_config = ConfigDict(frozen=True)

    type: Literal["span"] = "span"
    trial_id: str
    timestamp: datetime
    category: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class WSTrialEndedMessage(BaseModel):
    """WebSocket notification that a trial has ended."""

    model_config = ConfigDict(frozen=True)

    type: Literal["trial_ended"] = "trial_ended"
    trial_id: str
    timestamp: datetime


class WSSnapshotMessage(BaseModel):
    """WebSocket initial snapshot of all spans."""

    model_config = ConfigDict(frozen=True)

    type: Literal["snapshot"] = "snapshot"
    trial_id: str
    timestamp: datetime
    data: dict[str, list[dict[str, Any]]]


class WSHeartbeatMessage(BaseModel):
    """WebSocket keepalive."""

    model_config = ConfigDict(frozen=True)

    type: Literal["heartbeat"] = "heartbeat"
    timestamp: datetime


class WSStreamStatusMessage(BaseModel):
    """Stream status message for pause/resume feedback on live streams."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    type: Literal["stream_status"] = "stream_status"
    is_paused: bool = Field(serialization_alias="isPaused")
    buffer_size: int = Field(default=0, serialization_alias="bufferSize")
    buffered_count: int = Field(default=0, serialization_alias="bufferedCount")
    timestamp: datetime


class WSReplayMetaInfoMessage(BaseModel):
    """Replay metadata sent at the beginning of replay connection."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    type: Literal["replay_meta_info"] = "replay_meta_info"
    trial_id: str = Field(serialization_alias="trialId")
    total_items: int = Field(serialization_alias="totalItems")
    total_play_count: int = Field(serialization_alias="totalPlayCount")
    periods: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: datetime


class WSReplayStatusMessage(BaseModel):
    """Replay-specific status message with playback progress."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    type: Literal["replay_status"] = "replay_status"
    current_index: int = Field(serialization_alias="currentIndex")
    total_items: int = Field(serialization_alias="totalItems")
    current_play_index: int = Field(default=0, serialization_alias="currentPlayIndex")
    total_play_count: int = Field(default=0, serialization_alias="totalPlayCount")
    is_paused: bool = Field(serialization_alias="isPaused")
    speed: float  # 1.0, 2.0, 4.0
    progress_percent: float = Field(serialization_alias="progressPercent")
    timestamp: datetime


class WSReplayUnavailableMessage(BaseModel):
    """Sent when replay is not available for a trial."""

    model_config = ConfigDict(frozen=True)

    type: Literal["replay_unavailable"] = "replay_unavailable"
    trial_id: str
    reason: Literal["trial_not_found", "trial_still_running", "no_data"]
    timestamp: datetime


class ReplayResponse(BaseModel):
    """Response for POST /api/trials/{trial_id}/replay."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    trial_id: str = Field(serialization_alias="trialId")
    available: bool
    reason: None | Literal["trial_not_found", "trial_still_running", "no_data"]
    items: list[dict[str, Any]] = Field(default_factory=list)
    total_items: int = Field(default=0, serialization_alias="totalItems")


__all__ = [
    # API Response Models
    "AgentActionsResponse",
    "BetSummary",
    "GameCardData",
    "GamesResponse",
    "LandingResponse",
    "LeaderboardResponse",
    "ReplayResponse",
    "StatsResponse",
    "TrialDetailResponse",
    "TrialListItem",
    # WebSocket Models
    "WSHeartbeatMessage",
    "WSReplayMetaInfoMessage",
    "WSReplayStatusMessage",
    "WSReplayUnavailableMessage",
    "WSSnapshotMessage",
    "WSSpanMessage",
    "WSStreamStatusMessage",
    "WSTrialEndedMessage",
]
