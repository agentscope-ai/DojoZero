"""Gateway API request/response models.

Pydantic models for the Agent Gateway HTTP API. Follows patterns from
arena_server/_models.py with camelCase JSON serialization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Registration Models
# ============================================================================


class AgentRegistrationRequest(BaseModel):
    """Request body for agent registration."""

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(alias="agentId")
    persona: str = ""
    model: str = ""
    initial_balance: str | None = Field(default=None, alias="initialBalance")


class AgentRegistrationResponse(BaseModel):
    """Response for agent registration."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    agent_id: str = Field(serialization_alias="agentId")
    trial_id: str = Field(serialization_alias="trialId")
    balance: str
    registered_at: datetime = Field(serialization_alias="registeredAt")


# ============================================================================
# Trial Metadata Models
# ============================================================================


class TrialMetadataResponse(BaseModel):
    """Response for trial metadata."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    trial_id: str = Field(serialization_alias="trialId")
    phase: str
    sport_type: str = Field(default="", serialization_alias="sportType")
    game_id: str = Field(default="", serialization_alias="gameId")
    home_team: str = Field(default="", serialization_alias="homeTeam")
    away_team: str = Field(default="", serialization_alias="awayTeam")
    game_time: str | None = Field(default=None, serialization_alias="gameTime")
    metadata: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Event Models
# ============================================================================


class EventEnvelope(BaseModel):
    """Envelope wrapping an event for SSE/REST delivery."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    type: Literal["event"] = "event"
    trial_id: str = Field(serialization_alias="trialId")
    sequence: int
    timestamp: datetime
    payload: dict[str, Any]


class RecentEventsResponse(BaseModel):
    """Response for recent events polling."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    events: list[EventEnvelope]
    current_sequence: int = Field(serialization_alias="currentSequence")


# ============================================================================
# Odds Models
# ============================================================================


class SpreadLine(BaseModel):
    """Spread betting line."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    home_probability: float = Field(serialization_alias="homeProbability")
    away_probability: float = Field(serialization_alias="awayProbability")


class TotalLine(BaseModel):
    """Total (over/under) betting line."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    over_probability: float = Field(serialization_alias="overProbability")
    under_probability: float = Field(serialization_alias="underProbability")


class CurrentOddsResponse(BaseModel):
    """Response for current odds."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    event_id: str = Field(serialization_alias="eventId")
    home_probability: float | None = Field(
        default=None, serialization_alias="homeProbability"
    )
    away_probability: float | None = Field(
        default=None, serialization_alias="awayProbability"
    )
    spread_lines: dict[str, SpreadLine] = Field(
        default_factory=dict,
        serialization_alias="spreadLines",
    )
    total_lines: dict[str, TotalLine] = Field(
        default_factory=dict,
        serialization_alias="totalLines",
    )
    last_update: datetime | None = Field(default=None, serialization_alias="lastUpdate")
    betting_open: bool = Field(default=False, serialization_alias="bettingOpen")


# ============================================================================
# Betting Models
# ============================================================================


class BetRequest(BaseModel):
    """Request body for placing a bet."""

    model_config = ConfigDict(populate_by_name=True)

    market: Literal["moneyline", "spread", "total"]
    selection: Literal["home", "away", "over", "under"]
    amount: str  # Decimal as string for precision
    order_type: Literal["market", "limit"] = Field(
        default="market",
        alias="orderType",
    )
    limit_probability: float | None = Field(
        default=None,
        alias="limitProbability",
    )
    spread_value: float | None = Field(default=None, alias="spreadValue")
    total_value: float | None = Field(default=None, alias="totalValue")
    reference_sequence: int | None = Field(
        default=None,
        alias="referenceSequence",
        description="Sequence number of event this bet is based on (for staleness check)",
    )
    idempotency_key: str | None = Field(
        default=None,
        alias="idempotencyKey",
    )


class BetResponse(BaseModel):
    """Response for a placed bet."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    bet_id: str = Field(serialization_alias="betId")
    agent_id: str = Field(serialization_alias="agentId")
    event_id: str = Field(serialization_alias="eventId")
    market: str
    selection: str
    amount: str
    probability: str
    shares: str
    status: str
    created_at: datetime = Field(serialization_alias="createdAt")


class BetsListResponse(BaseModel):
    """Response for listing agent's bets."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    bets: list[BetResponse]


class HoldingResponse(BaseModel):
    """Response for a single holding."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    event_id: str = Field(serialization_alias="eventId")
    selection: str
    bet_type: str = Field(serialization_alias="betType")
    shares: str
    avg_probability: str = Field(serialization_alias="avgProbability")
    spread_value: str | None = Field(default=None, serialization_alias="spreadValue")
    total_value: str | None = Field(default=None, serialization_alias="totalValue")


class BalanceResponse(BaseModel):
    """Response for agent balance."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    agent_id: str = Field(serialization_alias="agentId")
    balance: str
    holdings: list[HoldingResponse] = Field(default_factory=list)


# ============================================================================
# Error Models
# ============================================================================


class ErrorDetail(BaseModel):
    """Error detail structure."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Standard error response format."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    error: ErrorDetail


class ErrorCodes:
    """Error code constants."""

    # Auth errors
    AUTH_REQUIRED = "AUTH_REQUIRED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    INVALID_TOKEN = "INVALID_TOKEN"

    # Rate limiting
    RATE_LIMITED = "RATE_LIMITED"

    # Registration
    ALREADY_REGISTERED = "ALREADY_REGISTERED"
    NOT_REGISTERED = "NOT_REGISTERED"

    # Betting errors
    BET_REJECTED = "BET_REJECTED"
    BETTING_CLOSED = "BETTING_CLOSED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    STALE_REFERENCE = "STALE_REFERENCE"
    DUPLICATE_BET = "DUPLICATE_BET"
    INVALID_MARKET = "INVALID_MARKET"
    INVALID_SELECTION = "INVALID_SELECTION"

    # Trial errors
    TRIAL_NOT_FOUND = "TRIAL_NOT_FOUND"
    TRIAL_NOT_RUNNING = "TRIAL_NOT_RUNNING"


# ============================================================================
# SSE Message Models
# ============================================================================


class HeartbeatMessage(BaseModel):
    """Heartbeat message for SSE connection keep-alive."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    type: Literal["heartbeat"] = "heartbeat"
    timestamp: datetime


__all__ = [
    # Registration
    "AgentRegistrationRequest",
    "AgentRegistrationResponse",
    # Trial
    "TrialMetadataResponse",
    # Events
    "EventEnvelope",
    "RecentEventsResponse",
    # Odds
    "CurrentOddsResponse",
    "SpreadLine",
    "TotalLine",
    # Betting
    "BetRequest",
    "BetResponse",
    "BetsListResponse",
    "BalanceResponse",
    "HoldingResponse",
    # Errors
    "ErrorCodes",
    "ErrorDetail",
    "ErrorResponse",
    # SSE
    "HeartbeatMessage",
]
