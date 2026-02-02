"""Tests for core._models: typed span deserialization.

Verifies that each span category is correctly deserialized from raw SpanData
into the appropriate Pydantic model via deserialize_span().

Span categories handled:
- trial.started/stopped/terminated → TrialLifecycleSpan
- event.* → DataEvent subclasses (from data/_models.py)
- *result*, *payout* → BettingResultSpan
- Other spans → None (not recognized)
"""

import json

from dojozero.core._models import (
    BettingResultSpan,
    TrialLifecycleSpan,
    deserialize_span,
)
from dojozero.core._tracing import SpanData
from dojozero.data._models import GameInitializeEvent


def _make_span(
    operation_name: str,
    tags: dict | None = None,
    start_time: int = 1_000_000,
) -> SpanData:
    """Helper to construct a SpanData with defaults."""
    return SpanData(
        trace_id="test-trial",
        span_id="span-001",
        operation_name=operation_name,
        start_time=start_time,
        duration=0,
        tags=tags or {},
    )


# ---------------------------------------------------------------------------
# TrialLifecycleSpan
# ---------------------------------------------------------------------------


class TestTrialLifecycleSpan:
    def test_trial_started_with_metadata(self):
        span = _make_span(
            "trial.started",
            tags={
                "dojozero.trial.phase": "started",
                "trial.home_team_tricode": "BOS",
                "trial.away_team_tricode": "TOR",
                "trial.home_team_name": "Boston Celtics",
                "trial.away_team_name": "Toronto Raptors",
                "trial.league": "NBA",
                "trial.game_date": "2025-03-15",
                "trial.sport_type": "nba",
                "trial.espn_game_id": "401584700",
                # Unknown metadata field -> extra_metadata
                "trial.custom_field": "custom_value",
            },
            start_time=5_000_000,
        )
        result = deserialize_span(span)
        assert isinstance(result, TrialLifecycleSpan)
        assert result.phase == "started"
        assert result.start_time == 5_000_000
        assert result.home_team_tricode == "BOS"
        assert result.away_team_tricode == "TOR"
        assert result.home_team_name == "Boston Celtics"
        assert result.league == "NBA"
        assert result.espn_game_id == "401584700"
        assert result.extra_metadata == {"custom_field": "custom_value"}

    def test_trial_stopped(self):
        span = _make_span("trial.stopped", tags={"dojozero.trial.phase": "stopped"})
        result = deserialize_span(span)
        assert isinstance(result, TrialLifecycleSpan)
        assert result.phase == "stopped"

    def test_trial_terminated(self):
        span = _make_span("trial.terminated")
        result = deserialize_span(span)
        assert isinstance(result, TrialLifecycleSpan)
        assert result.phase == "terminated"

    def test_phase_fallback_from_operation_name(self):
        """When dojozero.trial.phase tag is missing, extract from op name."""
        span = _make_span("trial.started", tags={})
        result = deserialize_span(span)
        assert isinstance(result, TrialLifecycleSpan)
        assert result.phase == "started"


# ---------------------------------------------------------------------------
# BettingResultSpan
# ---------------------------------------------------------------------------


class TestBettingResultSpan:
    def test_result_span_with_payout(self):
        span = _make_span(
            "betting.result",
            tags={
                "agent.id": "agent-001",
                "agent.name": "BettingAgent",
                "payout": 150.0,
                "wager": 100.0,
                "won": "win",
            },
        )
        result = deserialize_span(span)
        assert isinstance(result, BettingResultSpan)
        assert result.agent_id == "agent-001"
        assert result.agent_name == "BettingAgent"
        assert result.payout == 150.0
        assert result.wager == 100.0
        assert result.won is True

    def test_result_span_with_alternate_tag_names(self):
        """Uses agent_id/profit/amount fallback tag names."""
        span = _make_span(
            "game_result_payout",
            tags={
                "agent_id": "agent-002",
                "agent_name": "SafeAgent",
                "profit": -50.0,
                "amount": 50.0,
                "result": "loss",
            },
        )
        result = deserialize_span(span)
        assert isinstance(result, BettingResultSpan)
        assert result.agent_id == "agent-002"
        assert result.payout == -50.0
        assert result.wager == 50.0
        assert result.won is False

    def test_won_boolean_true(self):
        span = _make_span("payout_result", tags={"won": True})
        result = deserialize_span(span)
        assert isinstance(result, BettingResultSpan)
        assert result.won is True


# ---------------------------------------------------------------------------
# DataEvent dispatch
# ---------------------------------------------------------------------------


class TestDataEventDispatch:
    def test_game_initialize_event(self):
        """event.game_initialize dispatches to DataEvent deserialization."""
        span = _make_span(
            "event.game_initialize",
            tags={
                "event.game_id": "401584700",
                "event.sport": "nba",
                "event.home_team": json.dumps(
                    {"name": "Celtics", "tricode": "BOS", "color": "#007A33"}
                ),
                "event.away_team": json.dumps(
                    {"name": "Raptors", "tricode": "TOR", "color": "#CE1141"}
                ),
                "sport.type": "nba",
                "sequence": 1,
            },
        )
        result = deserialize_span(span)
        assert result is not None
        assert isinstance(result, GameInitializeEvent)
        assert result.game_id == "401584700"


# ---------------------------------------------------------------------------
# Dispatch: unrecognized → None
# ---------------------------------------------------------------------------


class TestUnrecognizedSpan:
    def test_unknown_operation_returns_none(self):
        span = _make_span("custom.unknown_operation", tags={"foo": "bar"})
        result = deserialize_span(span)
        assert result is None

    def test_empty_tags_returns_none(self):
        span = _make_span("something.else", tags={})
        result = deserialize_span(span)
        assert result is None

    def test_agent_spans_not_handled(self):
        """Agent spans (agent.*) are no longer deserialized - return None."""
        span = _make_span("agent.response", tags={"event.content": "Hello"})
        result = deserialize_span(span)
        assert result is None

    def test_broker_spans_not_handled(self):
        """Broker spans are no longer deserialized - return None."""
        span = _make_span("broker.state_update", tags={"broker.change_type": "bet"})
        result = deserialize_span(span)
        assert result is None

    def test_registration_spans_not_handled(self):
        """Actor registration spans are no longer deserialized - return None."""
        span = _make_span("agent.registered", tags={"actor.id": "a1"})
        result = deserialize_span(span)
        assert result is None
