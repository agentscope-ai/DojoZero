"""Typed Pydantic models for spans, API responses, and display.

This module provides typed models for two complementary purposes:

**Span deserialization** — converting raw ``SpanData`` from the tracing layer
into typed models so consumers (arena server, dashboards) get IDE autocomplete
and type safety instead of manual ``span.tags.get()`` calls.

**API contracts** — shared models (AgentInfo, AgentAction, LeaderboardEntry)
used by both agents (to assemble structured span data) and the arena server
(to serve typed JSON to the frontend).

Span Categories
---------------
TrialLifecycleSpan   trial.started / trial.stopped / trial.terminated
AgentMessageSpan     agent.input / agent.response / agent.tool_result
BrokerStateSpan      broker.state_update
ActorRegistrationSpan  *.registered
BettingResultSpan    operation_name contains "result" or "payout"
DataEvent            event.* prefix (delegated to deserialize_event_from_span)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from dojozero.core._tracing import SpanData, deserialize_event_from_span

if TYPE_CHECKING:
    from dojozero.data._models import DataEvent, TeamIdentity

logger = logging.getLogger(__name__)

# ============================================================================
# API / Display Models
# ============================================================================


class AgentInfo(BaseModel):
    """Agent display info for frontend."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    avatar: str
    color: str
    model: str = "AI Agent"


class AgentAction(BaseModel):
    """A single agent action for the live ticker."""

    model_config = ConfigDict(frozen=True)

    id: str
    agent: AgentInfo
    action: str
    time: str  # "5s ago", "2m ago"
    timestamp: int  # microseconds since epoch (for sorting)


class LeaderboardEntry(BaseModel):
    """An agent's leaderboard row."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    agent: AgentInfo
    winnings: float
    win_rate: float = Field(alias="winRate")
    total_bets: int = Field(alias="totalBets")
    roi: float
    rank: int = 0


# ============================================================================
# Trial Lifecycle
# ============================================================================

# Known metadata fields from BettingTrialMetadata (trial.* tags)
_TRIAL_METADATA_FIELDS = frozenset(
    {
        "home_team_tricode",
        "away_team_tricode",
        "home_team_name",
        "away_team_name",
        "league",
        "game_date",
        "sport_type",
        "espn_game_id",
    }
)


class TrialLifecycleSpan(BaseModel):
    """Deserialized trial.started / trial.stopped / trial.terminated span."""

    model_config = ConfigDict(frozen=True)

    category: Literal["trial_lifecycle"] = "trial_lifecycle"
    phase: str = ""  # "started", "stopped", "terminated"
    start_time: int = 0  # microseconds since epoch

    # Metadata from trial.* tags (BettingTrialMetadata fields)
    home_team_tricode: str = ""
    away_team_tricode: str = ""
    home_team_name: str = ""
    away_team_name: str = ""
    league: str = ""
    game_date: str = ""
    sport_type: str = ""
    espn_game_id: str = ""

    # Catch-all for forward compatibility with new metadata fields
    extra_metadata: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Agent Messages
# ============================================================================


class AgentMessageSpan(BaseModel):
    """Deserialized agent.input / agent.response / agent.tool_result span."""

    model_config = ConfigDict(frozen=True)

    category: Literal["agent_message"] = "agent_message"
    operation: str = ""  # agent.input, agent.response, agent.tool_result
    start_time: int = 0
    actor_id: str = ""
    sequence: int = 0
    stream_id: str = ""
    role: str = ""  # "user", "assistant", "system"
    name: str = ""
    content: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_id: str = ""
    message_id: str = ""
    game_id: str = ""


# ============================================================================
# Broker State
# ============================================================================


class BrokerStateSpan(BaseModel):
    """Deserialized broker.state_update span."""

    model_config = ConfigDict(frozen=True)

    category: Literal["broker_state"] = "broker_state"
    start_time: int = 0
    actor_id: str = ""
    change_type: str = ""  # bet_placed, bet_executed, bet_settled, ...
    accounts_count: int = 0
    bets_count: int = 0
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    bets: list[dict[str, Any]] = Field(default_factory=list)


# ============================================================================
# Actor Registration
# ============================================================================


class ActorRegistrationSpan(BaseModel):
    """Deserialized *.registered span (agent, datastream, operator)."""

    model_config = ConfigDict(frozen=True)

    category: Literal["actor_registration"] = "actor_registration"
    start_time: int = 0
    actor_id: str = ""
    actor_type: str = ""  # "agent", "datastream", "operator"
    name: str = ""
    model: str = ""
    model_provider: str = ""
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    source_type: str = ""


# ============================================================================
# Betting Results
# ============================================================================


class BettingResultSpan(BaseModel):
    """Deserialized span with betting result/payout data."""

    model_config = ConfigDict(frozen=True)

    category: Literal["betting_result"] = "betting_result"
    operation: str = ""
    start_time: int = 0
    span_id: str = ""
    agent_id: str = ""
    agent_name: str = ""
    payout: float = 0.0
    wager: float = 0.0
    won: bool = False


# ============================================================================
# Union type
# ============================================================================

SpanModel = Union[
    TrialLifecycleSpan,
    AgentMessageSpan,
    BrokerStateSpan,
    ActorRegistrationSpan,
    BettingResultSpan,
    "DataEvent",
]


# ============================================================================
# Deserialization helpers
# ============================================================================


def _json_parse(value: Any) -> Any:
    """Try to JSON-parse a string value; return as-is on failure."""
    if isinstance(value, str) and value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    return value


def _deserialize_trial_lifecycle(span: SpanData) -> TrialLifecycleSpan:
    """Deserialize a trial.started/stopped/terminated span."""
    tags = span.tags
    phase = tags.get("dojozero.trial.phase", "")
    if not phase:
        # Fallback: extract from operation_name "trial.<phase>"
        phase = span.operation_name.removeprefix("trial.")

    kwargs: dict[str, Any] = {
        "phase": phase,
        "start_time": span.start_time,
    }
    extra: dict[str, Any] = {}

    for key, value in tags.items():
        if not key.startswith("trial."):
            continue
        field = key[6:]  # Remove "trial." prefix
        if field in _TRIAL_METADATA_FIELDS:
            kwargs[field] = str(value) if value is not None else ""
        elif field != "phase":
            extra[field] = value

    kwargs["extra_metadata"] = extra
    return TrialLifecycleSpan(**kwargs)


def _deserialize_agent_message(span: SpanData) -> AgentMessageSpan:
    """Deserialize an agent.input/response/tool_result span."""
    tags = span.tags
    tool_calls_raw = tags.get("event.tool_calls")
    tool_calls: list[dict[str, Any]] = []
    if tool_calls_raw:
        parsed = _json_parse(tool_calls_raw)
        if isinstance(parsed, list):
            tool_calls = parsed

    return AgentMessageSpan(
        operation=span.operation_name,
        start_time=span.start_time,
        actor_id=str(tags.get("actor.id", "")),
        sequence=int(tags.get("sequence", 0)),
        stream_id=str(tags.get("event.stream_id", "")),
        role=str(tags.get("event.role", "")),
        name=str(tags.get("event.name", "")),
        content=str(tags.get("event.content", "")),
        tool_calls=tool_calls,
        tool_call_id=str(tags.get("event.tool_call_id", "")),
        message_id=str(tags.get("event.message_id", "")),
        game_id=str(tags.get("game.id", "")),
    )


def _deserialize_broker_state(span: SpanData) -> BrokerStateSpan:
    """Deserialize a broker.state_update span."""
    tags = span.tags

    accounts_raw = _json_parse(tags.get("broker.accounts", "{}"))
    bets_raw = _json_parse(tags.get("broker.bets", "{}"))

    # Broker stores accounts/bets as JSON dicts keyed by ID; normalize to lists
    accounts_list: list[dict[str, Any]] = []
    if isinstance(accounts_raw, dict):
        accounts_list = list(accounts_raw.values())
    elif isinstance(accounts_raw, list):
        accounts_list = accounts_raw

    bets_list: list[dict[str, Any]] = []
    if isinstance(bets_raw, dict):
        bets_list = list(bets_raw.values())
    elif isinstance(bets_raw, list):
        bets_list = bets_raw

    return BrokerStateSpan(
        start_time=span.start_time,
        actor_id=str(tags.get("actor.id", "")),
        change_type=str(tags.get("broker.change_type", "")),
        accounts_count=int(tags.get("broker.accounts_count", 0)),
        bets_count=int(tags.get("broker.bets_count", 0)),
        accounts=accounts_list,
        bets=bets_list,
    )


def _deserialize_actor_registration(span: SpanData) -> ActorRegistrationSpan:
    """Deserialize a *.registered span."""
    tags = span.tags

    tools_raw = _json_parse(tags.get("resource.tools", "[]"))
    tools: list[str] = []
    if isinstance(tools_raw, list):
        tools = [str(t) for t in tools_raw]

    return ActorRegistrationSpan(
        start_time=span.start_time,
        actor_id=str(tags.get("actor.id", "")),
        actor_type=str(tags.get("actor.type", "")),
        name=str(tags.get("resource.name", "")),
        model=str(tags.get("resource.model", "")),
        model_provider=str(tags.get("resource.model_provider", "")),
        system_prompt=str(tags.get("resource.system_prompt", "")),
        tools=tools,
        source_type=str(tags.get("resource.source_type", "")),
    )


def _deserialize_betting_result(span: SpanData) -> BettingResultSpan:
    """Deserialize a betting result/payout span."""
    tags = span.tags

    won_raw = tags.get("won", tags.get("result", ""))
    won = won_raw in ("win", "won", True, "true", "True")

    return BettingResultSpan(
        operation=span.operation_name,
        start_time=span.start_time,
        span_id=span.span_id,
        agent_id=str(tags.get("agent.id", tags.get("agent_id", ""))),
        agent_name=str(tags.get("agent.name", tags.get("agent_name", ""))),
        payout=float(tags.get("payout", tags.get("profit", 0))),
        wager=float(tags.get("wager", tags.get("amount", 0))),
        won=won,
    )


# ============================================================================
# Unified deserializer
# ============================================================================

# Operation names that map to trial lifecycle
_TRIAL_OPS = frozenset({"trial.started", "trial.stopped", "trial.terminated"})

# Operation names that map to agent messages
_AGENT_MESSAGE_OPS = frozenset({"agent.input", "agent.response", "agent.tool_result"})


def deserialize_span(span: SpanData) -> SpanModel | None:
    """Deserialize a SpanData into a typed model based on operation_name.

    Dispatch order:
        1. trial.started/stopped/terminated -> TrialLifecycleSpan
        2. agent.input/response/tool_result -> AgentMessageSpan
        3. broker.state_update -> BrokerStateSpan
        4. *.registered -> ActorRegistrationSpan
        5. "result" or "payout" in name -> BettingResultSpan
        6. event.* prefix -> DataEvent (via deserialize_event_from_span)
        7. Unrecognized -> None
    """
    op = span.operation_name

    if op in _TRIAL_OPS:
        return _deserialize_trial_lifecycle(span)

    if op in _AGENT_MESSAGE_OPS:
        return _deserialize_agent_message(span)

    if op == "broker.state_update":
        return _deserialize_broker_state(span)

    if op.endswith(".registered"):
        return _deserialize_actor_registration(span)

    # Betting result spans: fuzzy match on operation name
    if "result" in op or "payout" in op:
        return _deserialize_betting_result(span)

    # DataEvent spans: event.* prefix
    if op.startswith("event."):
        return deserialize_event_from_span(span)

    return None


# ============================================================================
# WebSocket serialization helper
# ============================================================================


def serialize_span_for_ws(model: SpanModel) -> dict[str, Any]:
    """Serialize a deserialized span model to a WebSocket-ready dict.

    Returns ``{"category": "<category>", "data": {...}}`` where ``data``
    contains the typed fields from the Pydantic model.

    DataEvent instances use ``category = "event"`` and serialize via
    ``to_dict()`` (which includes ``event_type``).  All other span models
    use their ``category`` Literal field and serialize via ``model_dump()``.
    """
    # Import lazily to avoid circular import at module level
    from dojozero.data._models import DataEvent

    if isinstance(model, DataEvent):
        return {"category": "event", "data": model.to_dict()}
    else:
        return {"category": model.category, "data": model.model_dump(mode="json")}


# ============================================================================
# Arena API Response Models
# ============================================================================


class StatsResponse(BaseModel):
    """Hero section stats for /api/stats and /api/landing."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    games_played: int = Field(default=0, serialization_alias="gamesPlayed")
    live_now: int = Field(default=0, serialization_alias="liveNow")
    wagered_today: int = Field(default=0, serialization_alias="wageredToday")


def _empty_team_identity() -> TeamIdentity:
    """Lazy factory for TeamIdentity default to avoid circular import."""
    from dojozero.data._models import TeamIdentity as _TI

    return _TI()


class GameCardData(BaseModel):
    """Single game card for landing/games pages."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str
    league: str = ""
    home_team: TeamIdentity = Field(
        default_factory=_empty_team_identity,
        serialization_alias="homeTeam",
    )
    away_team: TeamIdentity = Field(
        default_factory=_empty_team_identity,
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
    # API / Display Models
    "AgentAction",
    "AgentActionsResponse",
    "AgentInfo",
    "GameCardData",
    "GamesResponse",
    "LandingResponse",
    "LeaderboardEntry",
    "LeaderboardResponse",
    "StatsResponse",
    "TrialDetailResponse",
    "TrialListItem",
    # Span Models
    "ActorRegistrationSpan",
    "AgentMessageSpan",
    "BettingResultSpan",
    "BrokerStateSpan",
    "SpanModel",
    "TrialLifecycleSpan",
    # WebSocket Models
    "WSHeartbeatMessage",
    "WSSnapshotMessage",
    "WSSpanMessage",
    "WSTrialEndedMessage",
    # Helpers
    "deserialize_span",
    "serialize_span_for_ws",
]
