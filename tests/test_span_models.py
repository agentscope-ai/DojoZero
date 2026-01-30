"""Tests for core._models: typed span deserialization.

Verifies that each span category is correctly deserialized from raw SpanData
into the appropriate Pydantic model via deserialize_span().
"""

import json

from dojozero.core._models import (
    ActorRegistrationSpan,
    AgentMessageSpan,
    BettingResultSpan,
    BrokerStateSpan,
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
# AgentMessageSpan
# ---------------------------------------------------------------------------


class TestAgentMessageSpan:
    def test_agent_response_with_content(self):
        span = _make_span(
            "agent.response",
            tags={
                "actor.id": "agent-001",
                "sequence": 5,
                "event.stream_id": "stream-1",
                "event.role": "assistant",
                "event.name": "BettingAgent",
                "event.content": "I recommend betting on BOS",
                "event.message_id": "msg-123",
                "game.id": "game-456",
            },
        )
        result = deserialize_span(span)
        assert isinstance(result, AgentMessageSpan)
        assert result.operation == "agent.response"
        assert result.actor_id == "agent-001"
        assert result.sequence == 5
        assert result.role == "assistant"
        assert result.name == "BettingAgent"
        assert result.content == "I recommend betting on BOS"
        assert result.game_id == "game-456"

    def test_agent_input(self):
        span = _make_span("agent.input", tags={"event.role": "user"})
        result = deserialize_span(span)
        assert isinstance(result, AgentMessageSpan)
        assert result.operation == "agent.input"
        assert result.role == "user"

    def test_agent_tool_result_with_tool_calls(self):
        tool_calls = [{"name": "get_odds", "arguments": {"game_id": "123"}}]
        span = _make_span(
            "agent.tool_result",
            tags={
                "event.tool_calls": json.dumps(tool_calls),
                "event.tool_call_id": "tc-1",
            },
        )
        result = deserialize_span(span)
        assert isinstance(result, AgentMessageSpan)
        assert result.operation == "agent.tool_result"
        assert result.tool_calls == tool_calls
        assert result.tool_call_id == "tc-1"


# ---------------------------------------------------------------------------
# BrokerStateSpan
# ---------------------------------------------------------------------------


class TestBrokerStateSpan:
    def test_broker_state_update(self):
        accounts = {"acc-1": {"balance": 1000}, "acc-2": {"balance": 500}}
        bets = {"bet-1": {"amount": 50, "team": "BOS"}}
        span = _make_span(
            "broker.state_update",
            tags={
                "actor.id": "broker-001",
                "broker.change_type": "bet_placed",
                "broker.accounts_count": 2,
                "broker.bets_count": 1,
                "broker.accounts": json.dumps(accounts),
                "broker.bets": json.dumps(bets),
            },
        )
        result = deserialize_span(span)
        assert isinstance(result, BrokerStateSpan)
        assert result.actor_id == "broker-001"
        assert result.change_type == "bet_placed"
        assert result.accounts_count == 2
        assert result.bets_count == 1
        assert len(result.accounts) == 2
        assert len(result.bets) == 1

    def test_broker_state_with_list_format(self):
        """Handles accounts/bets already as JSON arrays."""
        span = _make_span(
            "broker.state_update",
            tags={
                "broker.accounts": json.dumps([{"balance": 100}]),
                "broker.bets": json.dumps([{"amount": 10}]),
            },
        )
        result = deserialize_span(span)
        assert isinstance(result, BrokerStateSpan)
        assert len(result.accounts) == 1
        assert len(result.bets) == 1


# ---------------------------------------------------------------------------
# ActorRegistrationSpan
# ---------------------------------------------------------------------------


class TestActorRegistrationSpan:
    def test_agent_registered(self):
        span = _make_span(
            "agent.registered",
            tags={
                "actor.id": "agent-001",
                "actor.type": "agent",
                "resource.name": "BettingAgent",
                "resource.model": "gpt-4",
                "resource.model_provider": "openai",
                "resource.system_prompt": "You are a betting agent.",
                "resource.tools": json.dumps(["get_odds", "place_bet"]),
                "resource.source_type": "llm",
            },
        )
        result = deserialize_span(span)
        assert isinstance(result, ActorRegistrationSpan)
        assert result.actor_id == "agent-001"
        assert result.actor_type == "agent"
        assert result.name == "BettingAgent"
        assert result.model == "gpt-4"
        assert result.tools == ["get_odds", "place_bet"]

    def test_datastream_registered(self):
        span = _make_span(
            "datastream.registered",
            tags={"actor.id": "ds-001", "actor.type": "datastream"},
        )
        result = deserialize_span(span)
        assert isinstance(result, ActorRegistrationSpan)
        assert result.actor_type == "datastream"


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
