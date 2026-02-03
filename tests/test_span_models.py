"""Tests for core._models: typed span deserialization.

Verifies that each span category is correctly deserialized from raw SpanData
into the appropriate Pydantic model via deserialize_span().

Span categories handled:
- trial.started/stopped/terminated → TrialLifecycleSpan
- event.* → DataEvent subclasses (from data/_models.py)
- broker.bet → BetExecutedPayload
- broker.state_update → BrokerStateUpdate
- agent.response → AgentResponseMessage
- agent.registered → AgentList
- Other spans → None (not recognized)
"""

import json

from dojozero.betting._models import (
    AgentList,
    AgentResponseMessage,
    BetExecutedPayload,
    BrokerStateUpdate,
)
from dojozero.core._models import (
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
# BrokerSpan dispatch
# ---------------------------------------------------------------------------


class TestBrokerSpanDispatch:
    def test_broker_bet_span(self):
        """broker.bet dispatches to BetExecutedPayload."""
        span = _make_span(
            "broker.bet",
            tags={
                "broker.bet_id": "bet-001",
                "broker.agent_id": "agent-001",
                "broker.event_id": "event-001",
                "broker.selection": "home",
                "broker.amount": "100.00",
                "broker.execution_probability": "0.55",
                "broker.shares": "181.82",
                "broker.execution_time": "2025-03-15T10:30:00Z",
            },
        )
        result = deserialize_span(span)
        assert result is not None
        assert isinstance(result, BetExecutedPayload)
        assert result.bet_id == "bet-001"
        assert result.agent_id == "agent-001"
        assert result.selection == "home"
        assert result.amount == "100.00"

    def test_broker_state_update_span(self):
        """broker.state_update dispatches to BrokerStateUpdate."""
        span = _make_span(
            "broker.state_update",
            tags={
                "broker.change_type": "bet_placed",
                "broker.accounts_count": 2,
                "broker.bets_count": 5,
                "broker.accounts": json.dumps({}),
                "broker.bets": json.dumps({}),
            },
        )
        result = deserialize_span(span)
        assert result is not None
        assert isinstance(result, BrokerStateUpdate)
        assert result.change_type == "bet_placed"
        assert result.accounts_count == 2
        assert result.bets_count == 5


# ---------------------------------------------------------------------------
# AgentSpan dispatch
# ---------------------------------------------------------------------------


class TestAgentSpanDispatch:
    def test_agent_response_span(self):
        """agent.response dispatches to AgentResponseMessage."""
        span = _make_span(
            "agent.response",
            tags={
                "sequence": 1,
                "stream_id": "stream-001",
                "agent_id": "Claude",
                "content": "I'll bet on the home team.",
                "trigger": "odds_update",
                "game_id": "game-001",
                "cot_steps": json.dumps(
                    [{"step_type": "reasoning", "text": "Analyzing..."}]
                ),
                "bet_type": "MONEYLINE",
                "bet_amount": 100.0,
                "bet_selection": "home",
            },
        )
        result = deserialize_span(span)
        assert result is not None
        assert isinstance(result, AgentResponseMessage)
        assert result.agent_id == "Claude"
        assert result.content == "I'll bet on the home team."
        assert result.bet_type == "MONEYLINE"
        assert result.bet_amount == 100.0

    def test_agent_initialize_span(self):
        """agent.agent_initialize dispatches to AgentList."""
        span = _make_span(
            "agent.agent_initialize",
            tags={
                "agents": json.dumps(
                    [
                        {
                            "agent_id": "degen-claude-001",
                            "model": "claude",
                            "model_display_name": "claude",
                            "system_prompt": "You are a sports betting agent.",
                            "persona": "degen",
                            "cdn_url": "https://example.com/avatar.png",
                        }
                    ]
                ),
            },
        )
        result = deserialize_span(span)
        assert result is not None
        assert isinstance(result, AgentList)
        assert len(result.agents) == 1
        assert result.agents[0].agent_id == "degen-claude-001"
        assert result.agents[0].model == "claude"
        assert result.agents[0].persona == "degen"

    def test_unknown_agent_operation_returns_none(self):
        """Unknown agent.* operations return None."""
        span = _make_span("agent.unknown_action", tags={"foo": "bar"})
        result = deserialize_span(span)
        assert result is None


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

    def test_fuzzy_result_payout_not_handled(self):
        """Fuzzy matching on 'result'/'payout' is no longer supported."""
        span = _make_span("betting.result", tags={"payout": 100.0})
        result = deserialize_span(span)
        assert result is None  # No longer matched
