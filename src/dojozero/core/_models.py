"""Typed Pydantic models for spans, API responses, and display.

This module provides:

**API contracts** — shared models (AgentInfo, AgentAction, LeaderboardEntry)
used by the arena server to serve typed JSON to the frontend.

**Span deserialization** — converting raw SpanData into typed models.
For DataEvent spans (event.*), we reconstruct the original event types.
For trial/agent/broker spans, we use minimal internal models.

Span Categories:
- event.* → DataEvent subclasses (from data/_models.py)
- trial.started/stopped/terminated → TrialLifecycleSpan
- *result*, *payout* → BettingResultSpan (for leaderboard aggregation)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from dojozero.core._tracing import SpanData

if TYPE_CHECKING:
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
# Betting Result (for leaderboard aggregation)
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
# Union type for all internal span models
# ============================================================================

# SpanModel is either:
# - A DataEvent (from data._models, for event.* spans)
# - An internal span model (TrialLifecycleSpan, BettingResultSpan)
SpanModel = Union[
    TrialLifecycleSpan,
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


def _deserialize_data_event(span: SpanData) -> "DataEvent | None":
    """Reconstruct a DataEvent from span tags.

    Uses the span's operation_name as event_type and extracts
    event.* tags as event fields.
    """
    from dojozero.data import deserialize_data_event

    event_dict: dict[str, Any] = {"event_type": span.operation_name}

    # Debug: log first event to see tag structure
    if not hasattr(_deserialize_data_event, "_debug_logged"):
        _deserialize_data_event._debug_logged = True
        event_tags = {k: v for k, v in span.tags.items() if k.startswith("event.")}
        logger.info(
            "Deserializing event: op=%s, event_tags=%s, all_tags_keys=%s",
            span.operation_name,
            {k: str(v)[:50] for k, v in list(event_tags.items())[:5]},
            list(span.tags.keys())[:10],
        )

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
        3. "result" or "payout" in name → BettingResultSpan
        4. Unrecognized → None
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

    # Betting result spans: fuzzy match on operation name
    if "result" in op or "payout" in op:
        return _deserialize_betting_result(span)

    # Unknown span type
    logger.debug("Unknown span operation: %s", op)
    return None


# ============================================================================
# WebSocket serialization helper
# ============================================================================


def serialize_span_for_ws(model: SpanModel) -> dict[str, Any]:
    """Serialize a deserialized span model to a WebSocket-ready dict.

    Returns {"category": "<category>", "data": {...}} where data
    contains the typed fields serialized with camelCase aliases.

    DataEvent instances use their event_type as category.
    Internal span models use their category field.
    """
    from dojozero.data._models import DataEvent

    if isinstance(model, DataEvent):
        # Use event_type as category, serialize with camelCase aliases
        return {
            "category": model.event_type,
            "data": model.model_dump(mode="json", by_alias=True),
        }
    else:
        # Internal span model
        return {
            "category": model.category,
            "data": model.model_dump(mode="json", by_alias=True),
        }


__all__ = [
    # API / Display Models
    "AgentAction",
    "AgentInfo",
    "LeaderboardEntry",
    # Span Models
    "BettingResultSpan",
    "SpanModel",
    "TrialLifecycleSpan",
    # Helpers
    "deserialize_span",
    "serialize_span_for_ws",
]
