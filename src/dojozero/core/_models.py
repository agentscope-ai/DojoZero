"""Typed Pydantic models for spans, API responses, and display.

This module provides:

**Span deserialization** — converting raw SpanData into typed models.
For DataEvent spans (event.*), we reconstruct the original event types.
For trial/broker/agent spans, we use typed models from betting/_models.py.

**WebSocket serialization** — serializing span models to WebSocket-ready dicts.

Span Categories:
- event.* → DataEvent subclasses (from data/_models.py)
- trial.started/stopped/terminated → TrialLifecycleSpan
- broker.bet → BetExecutedPayload (from betting/_models.py)
- broker.state_update → BrokerStateUpdate (from betting/_models.py)
- broker.final_stats → StatisticsList (from betting/_models.py)
- agent.response → AgentResponseMessage (from betting/_models.py)
- agent.agent_initialize → AgentList (from betting/_models.py)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from dojozero.core._tracing import SpanData

if TYPE_CHECKING:
    from dojozero.betting._models import (
        AgentInfo,
        AgentList,
        AgentResponseMessage,
        BetExecutedPayload,
        BrokerStateUpdate,
        StatisticsList,
    )
    from dojozero.data._models import DataEvent

logger = logging.getLogger(__name__)


# ============================================================================
# API / Display Models
# ============================================================================


class AgentAction(BaseModel):
    """A single agent action for the live ticker.

    Embeds the full AgentResponseMessage for frontend display.
    The frontend calculates "time ago" from timestamp.
    """

    model_config = ConfigDict(frozen=True)

    agent: "AgentInfo"  # From betting/_models.py
    response: "AgentResponseMessage"  # Full message for display
    timestamp: int  # microseconds since epoch (for sorting)


class LeaderboardEntry(BaseModel):
    """An agent's leaderboard row."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    agent: "AgentInfo"  # From betting/_models.py
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
    game_date: str = ""
    sport_type: str = ""
    espn_game_id: str = ""

    # Catch-all for forward compatibility with new metadata fields
    extra_metadata: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Union type for all internal span models
# ============================================================================

# SpanModel is either:
# - A DataEvent (from data._models, for event.* spans)
# - An internal span model (TrialLifecycleSpan)
# - A broker span model (BetExecutedPayload, BrokerStateUpdate, StatisticsList)
# - An agent span model (AgentResponseMessage, AgentList)
# Note: Use string forward references to avoid circular imports
SpanModel = Union[
    TrialLifecycleSpan,
    "BetExecutedPayload",
    "BrokerStateUpdate",
    "StatisticsList",
    "AgentResponseMessage",
    "AgentList",
    "DataEvent",
]


# ============================================================================
# Deserialization
# ============================================================================

# Internal tag prefixes to skip when extracting model fields
_INTERNAL_TAG_PREFIXES = ("otel.", "dojozero.", "service.", "telemetry.")


def _json_parse(value: Any) -> Any:
    """Try to JSON-parse a string value; return as-is on failure."""
    if isinstance(value, str) and value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    return value


def _extract_tags(span: SpanData, prefix: str) -> dict[str, Any]:
    """Extract and parse tags from span, stripping the given prefix."""
    kwargs: dict[str, Any] = {}
    tag_prefix = f"{prefix}."
    prefix_len = len(tag_prefix)

    for key, value in span.tags.items():
        # Skip internal tags
        if key.startswith(_INTERNAL_TAG_PREFIXES):
            continue
        # Extract prefixed tags (strip prefix) or non-prefixed tags
        if key.startswith(tag_prefix):
            kwargs[key[prefix_len:]] = _json_parse(value)
        elif "." not in key:
            # Non-prefixed field (e.g., agent spans use raw field names)
            kwargs[key] = _json_parse(value)

    return kwargs


def _get_span_models() -> dict[str, type[SpanModel]]:
    """Lazy-load span model mapping to avoid circular imports."""
    from dojozero.betting._models import (
        AgentList,
        AgentResponseMessage,
        BetExecutedPayload,
        BrokerStateUpdate,
        StatisticsList,
    )

    return {
        # Broker spans
        "broker.bet": BetExecutedPayload,
        "broker.bet_executed": BrokerStateUpdate,
        "broker.final_stats": StatisticsList,
        # Agent spans
        "agent.response": AgentResponseMessage,
        "agent.agent_initialize": AgentList,
        "trial.started": TrialLifecycleSpan,
        "trial.stopped": TrialLifecycleSpan,
    }


def deserialize_span(span: SpanData) -> SpanModel | None:
    """Deserialize a SpanData into a typed model based on operation_name.

    Dispatch:
        - trial.* → TrialLifecycleSpan (special handling for metadata)
        - event.* → DataEvent (via deserialize_data_event)
        - broker.*/agent.* → model from _SPAN_MODELS mapping
        - Unrecognized → None
    """
    from dojozero.data import deserialize_data_event

    op = span.operation_name
    if not op or "." not in op:
        return None

    prefix = op.split(".", 1)[0]
    if prefix == "trial":
        tags = span.tags
        phase = tags.get("dojozero.trial.phase") or op.split(".", 1)[-1]
        kwargs: dict[str, Any] = {"phase": phase, "start_time": span.start_time}
        extra: dict[str, Any] = {}
        for key, value in tags.items():
            if key.startswith("trial."):
                field = key[6:]
                if field in _TRIAL_METADATA_FIELDS:
                    kwargs[field] = str(value) if value is not None else ""
                elif field != "phase":
                    extra[field] = value
        kwargs["extra_metadata"] = extra
        return TrialLifecycleSpan(**kwargs)

    # Event spans: use data module deserializer
    if prefix == "event":
        event_dict: dict[str, Any] = {"event_type": op}
        for key, value in span.tags.items():
            if key.startswith("event."):
                event_dict[key[6:]] = _json_parse(value)
        return deserialize_data_event(event_dict)

    # Broker/Agent/Trial spans: unified model lookup
    model_cls = _get_span_models().get(op)
    if model_cls is None:
        logger.debug("Unknown span operation: %s", op)
        return None

    try:
        return model_cls.model_validate(_extract_tags(span, prefix))
    except Exception as e:
        logger.warning("Failed to deserialize %s: %s", op, e)
        return None


# ============================================================================
# WebSocket serialization helper
# ============================================================================

# Class name → operation name mapping (for non-DataEvent models)
# Used to derive category via unified stripping logic: op_name.split(".", 1)[-1]
_OPERATION_NAME_MAP: dict[str, str] = {
    "BetExecutedPayload": "broker.bet",
    "BrokerStateUpdate": "broker.state_update",
    "StatisticsList": "broker.final_stats",
    "AgentResponseMessage": "agent.response",
    "AgentList": "agent.agent_initialize",
    "TrialLifecycleSpan": "trial.lifecycle",
}

# Sport unification for frontend: nba_play/nfl_play → play, nba_game_update/nfl_game_update → game_update
_SPORT_UNIFY_MAP: dict[str, str] = {
    "nba_play": "play",
    "nfl_play": "play",
    "nba_game_update": "game_update",
    "nfl_game_update": "game_update",
}


def serialize_span_for_ws(model: SpanModel) -> dict[str, Any]:
    """Serialize a span model to a WebSocket-ready dict.

    Returns {"category": "<category>", "data": {...}} where:
    - category is derived by stripping prefix from operation name
    - data contains the typed fields serialized in snake_case format

    All models use the same logic: strip "prefix." to get category.
    DataEvent uses event_type field; other models use _OPERATION_NAME_MAP.
    """
    from dojozero.data._models import DataEvent

    data = model.model_dump(mode="json")

    # Get operation name / raw category
    if isinstance(model, DataEvent):
        raw_category = model.event_type
    else:
        class_name = type(model).__name__
        raw_category = _OPERATION_NAME_MAP.get(class_name, class_name.lower())

    # Unified logic: strip prefix (e.g., "event.nba_play" → "nba_play")
    category = raw_category.split(".", 1)[-1] if "." in raw_category else raw_category

    # Unify NBA/NFL categories
    if category in _SPORT_UNIFY_MAP:
        category = _SPORT_UNIFY_MAP[category]

    return {"category": category, "data": data}


__all__ = [
    # API / Display Models
    "AgentAction",
    "LeaderboardEntry",
    # Span Models
    "SpanModel",
    "TrialLifecycleSpan",
    # Helpers
    "deserialize_span",
    "serialize_span_for_ws",
]
