"""Typed Pydantic models for spans, API responses, and display.

This module provides:

**API contracts** — shared models (AgentInfo, AgentAction, LeaderboardEntry)
used by the arena server to serve typed JSON to the frontend.

**Span deserialization** — converting raw SpanData into typed models.
For DataEvent spans (event.*), we reconstruct the original event types.
For trial/broker/agent spans, we use typed models from betting/_models.py.

Span Categories:
- event.* → DataEvent subclasses (from data/_models.py)
- trial.started/stopped/terminated → TrialLifecycleSpan
- broker.bet → BetExecutedPayload (from betting/_models.py)
- broker.state_update → BrokerStateUpdate (from betting/_models.py)
- agent.response → AgentResponseMessage (from betting/_models.py)
- agent.registered → AgentRegistrationPayload (from betting/_models.py)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Literal, Union, cast

from pydantic import BaseModel, ConfigDict, Field

from dojozero.core._tracing import SpanData

if TYPE_CHECKING:
    from dojozero.betting._models import (
        AgentRegistration,
        AgentResponseMessage,
        BetExecutedPayload,
        BrokerStateUpdate,
    )
    from dojozero.data._models import DataEvent

logger = logging.getLogger(__name__)

# ============================================================================
# API / Display Models (for aggregated API responses)
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
# Trial Lifecycle (internal span model for phase detection)
# ============================================================================

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
    """Deserialized trial.started/stopped/terminated span."""

    model_config = ConfigDict(frozen=True)

    category: Literal["trial_lifecycle"] = "trial_lifecycle"
    phase: str = ""  # "started", "stopped", "terminated"
    start_time: int = 0  # microseconds since epoch

    # Metadata from trial.* tags
    home_team_tricode: str = ""
    away_team_tricode: str = ""
    home_team_name: str = ""
    away_team_name: str = ""
    league: str = ""
    game_date: str = ""
    sport_type: str = ""
    espn_game_id: str = ""

    extra_metadata: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Union type for all internal span models
# ============================================================================

# SpanModel is either:
# - A DataEvent (from data._models, for event.* spans)
# - An internal span model (TrialLifecycleSpan)
# - A broker span model (BetExecutedPayload, BrokerStateUpdate from betting/_models.py)
# - An agent span model (AgentResponseMessage, AgentRegistrationPayload from betting/_models.py)
# Note: Use string forward references to avoid circular imports
SpanModel = Union[
    TrialLifecycleSpan,
    "BetExecutedPayload",
    "BrokerStateUpdate",
    "AgentResponseMessage",
    "AgentRegistration",
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
        phase = span.operation_name.removeprefix("trial.")

    kwargs: dict[str, Any] = {
        "phase": phase,
        "start_time": span.start_time,
    }
    extra: dict[str, Any] = {}

    for key, value in tags.items():
        if not key.startswith("trial."):
            continue
        field = key[6:]
        if field in _TRIAL_METADATA_FIELDS:
            kwargs[field] = str(value) if value is not None else ""
        elif field != "phase":
            extra[field] = value

    kwargs["extra_metadata"] = extra
    return TrialLifecycleSpan(**kwargs)


def _deserialize_broker_span(
    span: SpanData,
) -> "BetExecutedPayload | BrokerStateUpdate | None":
    """Deserialize a broker.* span into the appropriate model.

    Maps:
        broker.bet → BetExecutedPayload
        broker.state_update → BrokerStateUpdate
    """
    from dojozero.betting._models import BetExecutedPayload, BrokerStateUpdate

    broker_span_models: dict[str, type[BaseModel]] = {
        "broker.bet": BetExecutedPayload,
        "broker.state_update": BrokerStateUpdate,
    }

    op = span.operation_name
    model_cls = broker_span_models.get(op)
    if model_cls is None:
        logger.debug("No model registered for broker operation: %s", op)
        return None

    # Extract broker.* tags and build kwargs
    kwargs: dict[str, Any] = {}
    for key, value in span.tags.items():
        if not key.startswith("broker."):
            continue
        field_name = key[7:]  # Remove "broker." prefix
        kwargs[field_name] = _json_parse(value)

    try:
        result = model_cls.model_validate(kwargs)
        return cast("BetExecutedPayload | BrokerStateUpdate", result)
    except Exception as e:
        logger.warning("Failed to deserialize %s: %s", op, e)
        return None


def _deserialize_agent_span(
    span: SpanData,
) -> "AgentResponseMessage | AgentRegistration | None":
    """Deserialize an agent.* span into the appropriate model.

    Maps:
        agent.response → AgentResponseMessage
        agent.registered → AgentRegistration

    Note: agent.response tags are NOT prefixed - they use raw field names
    from AgentResponseMessage.model_dump().
    """
    from dojozero.betting._models import AgentRegistration, AgentResponseMessage

    agent_span_models: dict[str, type[BaseModel]] = {
        "agent.response": AgentResponseMessage,
        "agent.agent_initialize": AgentRegistration,
        "agent.registered": AgentRegistration,
    }

    op = span.operation_name
    model_cls = agent_span_models.get(op)
    if model_cls is None:
        logger.debug("No model registered for agent operation: %s", op)
        return None

    # Agent spans use non-prefixed tags (direct model_dump output)
    kwargs: dict[str, Any] = {}
    for key, value in span.tags.items():
        # Skip OTel/dojozero internal tags
        if key.startswith(("otel.", "dojozero.", "service.", "telemetry.")):
            continue
        kwargs[key] = _json_parse(value)

    try:
        result = model_cls.model_validate(kwargs)
        return cast("AgentResponseMessage | AgentRegistration", result)
    except Exception as e:
        logger.warning("Failed to deserialize %s: %s", op, e)
        return None


def _deserialize_data_event(span: SpanData) -> "DataEvent | None":
    """Reconstruct a DataEvent from span tags.

    Uses the span's operation_name as event_type and extracts
    event.* tags as event fields.
    """
    from dojozero.data import deserialize_data_event

    event_dict: dict[str, Any] = {"event_type": span.operation_name}

    for key, value in span.tags.items():
        if not key.startswith("event."):
            continue
        field_name = key[6:]  # Remove "event." prefix
        event_dict[field_name] = _json_parse(value)

    # Debug: log the final event_dict
    if len(event_dict) <= 1:
        logger.warning(
            "Event has no data fields: op=%s, tags_keys=%s",
            span.operation_name,
            list(span.tags.keys()),
        )

    return deserialize_data_event(event_dict)


# ============================================================================
# Unified deserializer
# ============================================================================

_TRIAL_OPS = frozenset({"trial.started", "trial.stopped", "trial.terminated"})


def deserialize_span(span: SpanData) -> SpanModel | None:
    """Deserialize a SpanData into a typed model based on operation_name.

    Dispatch order:
        1. trial.started/stopped/terminated → TrialLifecycleSpan
        2. event.* prefix → DataEvent (via deserialize_data_event)
        3. broker.* prefix → BetExecutedPayload or BrokerStateUpdate
        4. agent.* prefix → AgentResponseMessage or AgentRegistrationPayload
        5. Unrecognized → None
    """
    op = span.operation_name

    if not op:
        logger.debug("Span has empty operation_name, skipping")
        return None

    if op in _TRIAL_OPS:
        return _deserialize_trial_lifecycle(span)

    # DataEvent spans: event.* prefix
    if op.startswith("event."):
        result = _deserialize_data_event(span)
        if result is None:
            logger.debug("Failed to deserialize DataEvent: op=%s", op)
        return result

    # Broker spans: broker.* prefix
    if op.startswith("broker."):
        return _deserialize_broker_span(span)

    # Agent spans: agent.* prefix
    if op.startswith("agent."):
        return _deserialize_agent_span(span)

    # Unknown span type
    logger.debug("Unknown span operation: %s", op)
    return None


# ============================================================================
# WebSocket serialization helper
# ============================================================================


def serialize_span_for_ws(model: SpanModel) -> dict[str, Any]:
    """Serialize a deserialized span model to a WebSocket-ready dict.

    Returns {"category": "<category>", "data": {...}} where data
    contains the typed fields serialized in snake_case format.

    Category transformations:
    - Strip prefix for all xx.xx formats (e.g., event.xxx → xxx, broker.xxx → xxx)
    - Unify play events: nba_play/nfl_play → play (with sport in data)
    - Unify game_update events: nba_game_update/nfl_game_update → game_update (with sport in data)
    """
    from dojozero.betting._models import (
        AgentRegistration,
        AgentResponseMessage,
        BetExecutedPayload,
        BrokerStateUpdate,
    )
    from dojozero.data._models import DataEvent

    if isinstance(model, DataEvent):
        # Use event_type as base category
        raw_category = model.event_type

        # Strip prefix for xx.xx format (e.g., event.nba_play → nba_play)
        if "." in raw_category:
            category = raw_category.split(".", 1)[1]
        else:
            category = raw_category

        # Serialize with snake_case field names (by_alias=False is default)
        data = model.model_dump(mode="json")

        # Unify play and game_update categories: add sport field, normalize category
        if category in ("nba_play", "nfl_play"):
            # Extract sport from category prefix
            sport = "nba" if category == "nba_play" else "nfl"
            data["sport"] = sport
            category = "play"
        elif category in ("nba_game_update", "nfl_game_update"):
            # Extract sport from category prefix
            sport = "nba" if category == "nba_game_update" else "nfl"
            data["sport"] = sport
            category = "game_update"

        return {
            "category": category,
            "data": data,
        }
    elif isinstance(model, BetExecutedPayload):
        # broker.bet → "bet"
        return {
            "category": "bet",
            "data": model.model_dump(mode="json"),
        }
    elif isinstance(model, BrokerStateUpdate):
        # broker.state_update → "state_update"
        return {
            "category": "state_update",
            "data": model.model_dump(mode="json"),
        }
    elif isinstance(model, AgentResponseMessage):
        # agent.response → "response"
        return {
            "category": "response",
            "data": model.model_dump(mode="json"),
        }
    elif isinstance(model, AgentRegistration):
        # agent.registered → "registered"
        return {
            "category": "registered",
            "data": model.model_dump(mode="json"),
        }
    else:
        # Internal span model (TrialLifecycleSpan)
        # Strip prefix if present
        raw_category = model.category
        if "." in raw_category:
            category = raw_category.split(".", 1)[1]
        else:
            category = raw_category

        return {
            "category": category,
            "data": model.model_dump(mode="json"),
        }


__all__ = [
    # API / Display Models
    "AgentAction",
    "AgentInfo",
    "LeaderboardEntry",
    # Span Models
    "SpanModel",
    "TrialLifecycleSpan",
    # Helpers
    "deserialize_span",
    "serialize_span_for_ws",
]
