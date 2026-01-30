"""Typed Pydantic models for arena server API responses and WebSocket messages.

These are presentation-layer view models assembled by the arena server from
deserialized spans, static lookups, and computed aggregations.  They are
**not** SLS span types — they are cross-trial, frontend-facing contracts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    bets: list[dict[str, Any]] = Field(default_factory=list)
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
    timestamp: str
    category: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class WSTrialEndedMessage(BaseModel):
    """WebSocket notification that a trial has ended."""

    model_config = ConfigDict(frozen=True)

    type: Literal["trial_ended"] = "trial_ended"
    trial_id: str
    timestamp: str


class WSSnapshotMessage(BaseModel):
    """WebSocket initial snapshot of all spans."""

    model_config = ConfigDict(frozen=True)

    type: Literal["snapshot"] = "snapshot"
    trial_id: str
    timestamp: str
    data: dict[str, list[dict[str, Any]]]


class WSHeartbeatMessage(BaseModel):
    """WebSocket keepalive."""

    model_config = ConfigDict(frozen=True)

    type: Literal["heartbeat"] = "heartbeat"
    timestamp: str


__all__ = [
    # API Response Models
    "AgentActionsResponse",
    "GameCardData",
    "GamesResponse",
    "LandingResponse",
    "LeaderboardResponse",
    "StatsResponse",
    "TrialDetailResponse",
    "TrialListItem",
    # WebSocket Models
    "WSHeartbeatMessage",
    "WSSnapshotMessage",
    "WSSpanMessage",
    "WSTrialEndedMessage",
]
