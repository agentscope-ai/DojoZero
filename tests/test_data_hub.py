"""Tests for DataHub - the central event bus."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import Field

from dojozero.data._hub import DataHub, _EventEnvelope, _GamePhase
from dojozero.data._models import (
    DataEvent,
    GameInitializeEvent,
    GameResultEvent,
    OddsUpdateEvent,
    PreGameInsightEvent,
    extract_game_id,
    register_event,
)
from dojozero.data import GameStartEvent


# Test event classes - Pydantic models with Literal event_type
@register_event
class TestEvent(DataEvent):
    """Simple event for testing."""

    __test__ = False  # Prevent pytest from collecting this as a test class

    event_type: Literal["test_event"] = "test_event"
    event_id: str = ""
    value: str = ""


@register_event
class TestGameEvent(DataEvent):
    """Test event with game_id for lifecycle gate testing."""

    __test__ = False

    event_type: Literal["test_game_event"] = "test_game_event"
    game_id: str = ""
    sport: str = "test"
    value: str = ""


@register_event
class TestEventWithGameTime(DataEvent):
    """Event with game_time field for testing game_date extraction."""

    __test__ = False

    event_type: Literal["test_event_with_game_time"] = "test_event_with_game_time"
    event_id: str = ""
    game_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@register_event
class TestEventWithGameTimeUtc(DataEvent):
    """Event with game_time_utc string field for testing fallback."""

    __test__ = False

    event_type: Literal["test_event_with_game_time_utc"] = (
        "test_event_with_game_time_utc"
    )
    event_id: str = ""
    game_time_utc: str = ""


@register_event
class TestEventWithGameTimeStr(DataEvent):
    """Event with game_time as string field for testing ISO string extraction."""

    __test__ = False

    event_type: Literal["test_event_game_time_str"] = "test_event_game_time_str"
    event_id: str = ""
    game_time: str = ""  # type: ignore[assignment]


@pytest.fixture
def temp_persistence_file():
    """Create a temporary file for event persistence."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        yield Path(f.name)
    # Cleanup handled by tempfile


@pytest.fixture
def hub(temp_persistence_file):
    """Create a DataHub instance for testing."""
    return DataHub(
        hub_id="test_hub",
        persistence_file=temp_persistence_file,
    )


def make_test_event(value: str = "test") -> TestEvent:
    """Helper to create test events."""
    return TestEvent(
        event_id=f"test_{value}",
        value=value,
    )


class TestDataHubSubscription:
    """Tests for agent subscription management."""

    def test_subscribe_agent_single_event_type(self, hub):
        """Test subscribing to a single event type."""
        callback = MagicMock()
        hub.subscribe_agent(
            agent_id="agent1",
            event_types=["test_event"],
            callback=callback,
        )

        assert "agent1" in hub._agent_subscriptions
        assert "test_event" in hub._agent_subscriptions["agent1"]

    def test_subscribe_agent_multiple_event_types(self, hub):
        """Test subscribing to multiple event types."""
        callback = MagicMock()
        hub.subscribe_agent(
            agent_id="agent1",
            event_types=["test_event", "other_event"],
            callback=callback,
        )

        assert "test_event" in hub._agent_subscriptions["agent1"]
        assert "other_event" in hub._agent_subscriptions["agent1"]

    def test_subscribe_multiple_agents_same_event(self, hub):
        """Test multiple agents subscribing to the same event type."""
        callback1 = MagicMock()
        callback2 = MagicMock()

        hub.subscribe_agent(
            agent_id="agent1", event_types=["test_event"], callback=callback1
        )
        hub.subscribe_agent(
            agent_id="agent2", event_types=["test_event"], callback=callback2
        )

        assert "test_event" in hub._agent_subscriptions["agent1"]
        assert "test_event" in hub._agent_subscriptions["agent2"]
        assert len(hub._event_handlers["test_event"]) == 2

    def test_unsubscribe_agent(self, hub):
        """Test unsubscribing an agent."""
        callback = MagicMock()
        hub.subscribe_agent(
            agent_id="agent1", event_types=["test_event"], callback=callback
        )

        hub.unsubscribe_agent("agent1")

        assert "agent1" not in hub._agent_subscriptions

    def test_unsubscribe_nonexistent_agent(self, hub):
        """Test unsubscribing a non-existent agent doesn't raise."""
        # Should not raise
        hub.unsubscribe_agent("nonexistent_agent")


class TestDataHubEventDispatch:
    """Tests for event reception and dispatch."""

    @pytest.mark.asyncio
    async def test_receive_event_dispatches_to_callback(self, hub):
        """Test that received events are dispatched to callbacks."""
        callback = MagicMock()
        hub.subscribe_agent(
            agent_id="agent1", event_types=["test_event"], callback=callback
        )

        event = make_test_event("dispatch_test")
        await hub.receive_event(event)

        callback.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_receive_event_dispatches_to_multiple_callbacks(self, hub):
        """Test that events are dispatched to all subscribed callbacks."""
        callback1 = MagicMock()
        callback2 = MagicMock()

        hub.subscribe_agent(
            agent_id="agent1", event_types=["test_event"], callback=callback1
        )
        hub.subscribe_agent(
            agent_id="agent2", event_types=["test_event"], callback=callback2
        )

        event = make_test_event("multi_callback")
        await hub.receive_event(event)

        callback1.assert_called_once_with(event)
        callback2.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_receive_event_only_dispatches_to_matching_type(self, hub):
        """Test that events only dispatch to callbacks for matching event types."""
        test_callback = MagicMock()
        other_callback = MagicMock()

        hub.subscribe_agent(
            agent_id="agent1", event_types=["test_event"], callback=test_callback
        )
        hub.subscribe_agent(
            agent_id="agent2", event_types=["other_event"], callback=other_callback
        )

        event = make_test_event("type_filter")
        await hub.receive_event(event)

        test_callback.assert_called_once_with(event)
        other_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_exception_doesnt_break_dispatch(self, hub):
        """Test that exception in one callback doesn't prevent others from being called."""
        failing_callback = MagicMock(side_effect=Exception("callback error"))
        success_callback = MagicMock()

        hub.subscribe_agent(
            agent_id="agent1", event_types=["test_event"], callback=failing_callback
        )
        hub.subscribe_agent(
            agent_id="agent2", event_types=["test_event"], callback=success_callback
        )

        event = make_test_event("exception_test")
        await hub.receive_event(event)

        failing_callback.assert_called_once()
        success_callback.assert_called_once_with(event)


class TestDataHubPersistence:
    """Tests for event persistence."""

    @pytest.mark.asyncio
    async def test_event_persisted_to_file(self, hub, temp_persistence_file):
        """Test that events are persisted to file."""
        event = make_test_event("persist_test")
        await hub.receive_event(event)

        # Read the file and verify
        with open(temp_persistence_file) as f:
            lines = f.readlines()

        assert len(lines) == 1
        persisted = json.loads(lines[0])
        assert persisted["event_id"] == "test_persist_test"
        assert persisted["value"] == "persist_test"

    @pytest.mark.asyncio
    async def test_multiple_events_persisted(self, hub, temp_persistence_file):
        """Test that multiple events are persisted."""
        for i in range(3):
            event = make_test_event(f"multi_{i}")
            await hub.receive_event(event)

        with open(temp_persistence_file) as f:
            lines = f.readlines()

        assert len(lines) == 3


class TestDataHubBacktest:
    """Tests for backtest mode."""

    @pytest.mark.asyncio
    async def test_start_backtest_loads_events(self, hub, temp_persistence_file):
        """Test that start_backtest loads events from file."""
        # First persist some events (use real union types so deserialization works)
        # Must follow lifecycle: GameInitializeEvent before GameStartEvent
        for i in range(3):
            await hub.receive_event(
                GameInitializeEvent(game_id=f"backtest_{i}", sport="nba")
            )
            await hub.receive_event(GameStartEvent(game_id=f"backtest_{i}"))

        # Create new hub and start backtest (uses same file for consistency)
        backtest_hub = DataHub(
            hub_id="backtest_hub", persistence_file=temp_persistence_file
        )
        await backtest_hub.start_backtest(temp_persistence_file)

        assert len(backtest_hub._backtest_events) == 6  # 3 init + 3 start
        assert backtest_hub._backtest_mode is True

    @pytest.mark.asyncio
    async def test_backtest_next_returns_events_in_order(
        self, hub, temp_persistence_file
    ):
        """Test that backtest_next returns events in order."""
        # Persist events (use real union types so deserialization works)
        for i in range(3):
            await hub.receive_event(
                GameInitializeEvent(game_id=f"order_{i}", sport="nba")
            )
            await hub.receive_event(GameStartEvent(game_id=f"order_{i}"))

        # Backtest (uses same file for consistency)
        backtest_hub = DataHub(
            hub_id="backtest_hub", persistence_file=temp_persistence_file
        )
        await backtest_hub.start_backtest(temp_persistence_file)

        # Events are sorted by timestamp — init and start pairs interleaved
        events = []
        while True:
            evt = await backtest_hub.backtest_next()
            if evt is None:
                break
            events.append(evt)

        assert len(events) == 6
        # GameStartEvents should all be present
        start_events = [e for e in events if isinstance(e, GameStartEvent)]
        assert len(start_events) == 3

    @pytest.mark.asyncio
    async def test_backtest_all_dispatches_all_events(self, hub, temp_persistence_file):
        """Test that backtest_all dispatches all events."""
        # Persist events (use real union types so deserialization works)
        for i in range(3):
            await hub.receive_event(
                GameInitializeEvent(game_id=f"all_{i}", sport="nba")
            )
            await hub.receive_event(GameStartEvent(game_id=f"all_{i}"))

        # Setup backtest hub with callback (uses same file for consistency)
        backtest_hub = DataHub(
            hub_id="backtest_hub", persistence_file=temp_persistence_file
        )
        start_callback = MagicMock()
        init_callback = MagicMock()
        backtest_hub.subscribe_agent(
            agent_id="agent1",
            event_types=["event.game_start"],
            callback=start_callback,
        )
        backtest_hub.subscribe_agent(
            agent_id="agent2",
            event_types=["event.game_initialize"],
            callback=init_callback,
        )

        await backtest_hub.start_backtest(temp_persistence_file)
        await backtest_hub.backtest_all()

        assert start_callback.call_count == 3
        assert init_callback.call_count == 3

    @pytest.mark.asyncio
    async def test_stop_backtest_clears_state(self, hub, temp_persistence_file):
        """Test that stop_backtest clears backtest state."""
        # Persist event
        await hub.receive_event(make_test_event("stop_test"))

        # Start and stop backtest (uses same file for consistency)
        backtest_hub = DataHub(
            hub_id="backtest_hub", persistence_file=temp_persistence_file
        )
        await backtest_hub.start_backtest(temp_persistence_file)
        backtest_hub.stop_backtest()

        assert backtest_hub._backtest_mode is False
        assert len(backtest_hub._backtest_events) == 0
        assert backtest_hub._backtest_index == 0

    @pytest.mark.asyncio
    async def test_backtest_missing_file_raises(self, hub):
        """Test that backtesting from missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            await hub.start_backtest("/nonexistent/file.jsonl")


class TestDataHubStoreConnection:
    """Tests for store connection."""

    def test_connect_store_sets_emitter(self, hub):
        """Test that connecting a store sets its async event emitter."""
        mock_store = MagicMock()
        mock_store._data_hub = None

        hub.connect_store(mock_store)

        mock_store.set_async_event_emitter.assert_called_once()
        assert mock_store in hub._connected_stores

    def test_connect_store_sets_hub_reference(self, hub):
        """Test that connecting a store sets the hub reference."""
        mock_store = MagicMock()
        mock_store._data_hub = None

        hub.connect_store(mock_store)

        assert mock_store._data_hub == hub

    def test_connect_same_store_twice(self, hub):
        """Test that connecting same store twice doesn't duplicate."""
        mock_store = MagicMock()
        mock_store._data_hub = None

        hub.connect_store(mock_store)
        hub.connect_store(mock_store)

        assert hub._connected_stores.count(mock_store) == 1


class TestDataHubLifecycle:
    """Tests for hub lifecycle management."""

    @pytest.mark.asyncio
    async def test_start_calls_store_start_polling(self, hub):
        """Test that start() calls start_polling on all connected stores."""

        async def noop():
            pass

        mock_store1 = MagicMock()
        mock_store1._data_hub = None
        mock_store1.start_polling = MagicMock(return_value=noop())

        mock_store2 = MagicMock()
        mock_store2._data_hub = None
        mock_store2.start_polling = MagicMock(return_value=noop())

        hub.connect_store(mock_store1)
        hub.connect_store(mock_store2)

        await hub.start()

        mock_store1.start_polling.assert_called_once()
        mock_store2.start_polling.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_calls_store_stop_polling(self, hub):
        """Test that stop() calls stop_polling on all connected stores."""

        async def noop():
            pass

        mock_store1 = MagicMock()
        mock_store1._data_hub = None
        mock_store1.stop_polling = MagicMock(return_value=noop())

        mock_store2 = MagicMock()
        mock_store2._data_hub = None
        mock_store2.stop_polling = MagicMock(return_value=noop())

        hub.connect_store(mock_store1)
        hub.connect_store(mock_store2)

        await hub.stop()

        mock_store1.stop_polling.assert_called_once()
        mock_store2.stop_polling.assert_called_once()


class TestDataHubSpanEmission:
    """Tests for SLS span emission and tag extraction."""

    @pytest.fixture
    def hub_with_trial_id(self, temp_persistence_file):
        """Create a DataHub with trial_id set for span emission."""
        return DataHub(
            hub_id="test_hub",
            persistence_file=temp_persistence_file,
            trial_id="test-trial-123",
        )

    @patch("dojozero.core._tracing.emit_span")
    @patch("dojozero.core._tracing.create_span_from_event")
    def test_game_date_extracted_from_game_time_datetime(
        self, mock_create_span, mock_emit_span, hub_with_trial_id
    ):
        """Test game_date is extracted from game_time datetime field."""
        mock_create_span.return_value = MagicMock()
        game_time = datetime(2025, 1, 21, 19, 30, 0, tzinfo=timezone.utc)
        event = TestEventWithGameTime(event_id="test_1", game_time=game_time)

        hub_with_trial_id._emit_event_span(
            event, actor_id="test_actor", sport_type="nba"
        )

        # Verify create_span_from_event was called with correct tags
        call_args = mock_create_span.call_args
        tags = call_args.kwargs["extra_tags"]
        assert tags["game.date"] == "2025-01-21"

    @patch("dojozero.core._tracing.emit_span")
    @patch("dojozero.core._tracing.create_span_from_event")
    def test_game_date_extracted_from_game_time_iso_string(
        self, mock_create_span, mock_emit_span, hub_with_trial_id
    ):
        """Test game_date is extracted from game_time ISO string field."""
        mock_create_span.return_value = MagicMock()
        event = TestEventWithGameTimeStr(
            event_id="test_2", game_time="2025-02-15T20:00:00+00:00"
        )

        hub_with_trial_id._emit_event_span(
            event, actor_id="test_actor", sport_type="nba"
        )

        call_args = mock_create_span.call_args
        tags = call_args.kwargs["extra_tags"]
        assert tags["game.date"] == "2025-02-15"

    @patch("dojozero.core._tracing.emit_span")
    @patch("dojozero.core._tracing.create_span_from_event")
    def test_game_date_fallback_to_game_time_utc(
        self, mock_create_span, mock_emit_span, hub_with_trial_id
    ):
        """Test game_date falls back to game_time_utc string field."""
        mock_create_span.return_value = MagicMock()
        event = TestEventWithGameTimeUtc(
            event_id="test_3", game_time_utc="2025-03-10T18:00:00Z"
        )

        hub_with_trial_id._emit_event_span(
            event, actor_id="test_actor", sport_type="nfl"
        )

        call_args = mock_create_span.call_args
        tags = call_args.kwargs["extra_tags"]
        assert tags["game.date"] == "2025-03-10"

    @patch("dojozero.core._tracing.emit_span")
    @patch("dojozero.core._tracing.create_span_from_event")
    def test_no_game_date_when_no_game_time_fields(
        self, mock_create_span, mock_emit_span, hub_with_trial_id
    ):
        """Test no game_date tag when event has no game_time fields."""
        mock_create_span.return_value = MagicMock()
        event = TestEvent(event_id="test_4", value="no_game_time")

        hub_with_trial_id._emit_event_span(
            event, actor_id="test_actor", sport_type="nba"
        )

        call_args = mock_create_span.call_args
        tags = call_args.kwargs["extra_tags"]
        assert "game.date" not in tags

    @patch("dojozero.core._tracing.emit_span")
    @patch("dojozero.core._tracing.create_span_from_event")
    def test_game_id_extracted_from_event(
        self, mock_create_span, mock_emit_span, hub_with_trial_id
    ):
        """Test game_id is extracted as top-level tag."""
        mock_create_span.return_value = MagicMock()
        event = TestEvent(event_id="401810490", value="test")

        hub_with_trial_id._emit_event_span(
            event, actor_id="test_actor", sport_type="nba"
        )

        call_args = mock_create_span.call_args
        tags = call_args.kwargs["extra_tags"]
        assert tags["game.id"] == "401810490"

    @patch("dojozero.core._tracing.emit_span")
    @patch("dojozero.core._tracing.create_span_from_event")
    def test_game_id_extracted_from_pbp_event_id(
        self, mock_create_span, mock_emit_span, hub_with_trial_id
    ):
        """Test game_id extracted from PBP-style event_id like '0022400608_pbp_188'."""
        mock_create_span.return_value = MagicMock()
        event = TestEvent(event_id="0022400608_pbp_188", value="pbp_test")

        hub_with_trial_id._emit_event_span(
            event, actor_id="test_actor", sport_type="nba"
        )

        call_args = mock_create_span.call_args
        tags = call_args.kwargs["extra_tags"]
        assert tags["game.id"] == "0022400608"

    @patch("dojozero.core._tracing.emit_span")
    @patch("dojozero.core._tracing.create_span_from_event")
    def test_game_id_from_store_takes_precedence(
        self, mock_create_span, mock_emit_span, hub_with_trial_id
    ):
        """Test game_id passed from store takes precedence over event payload."""
        mock_create_span.return_value = MagicMock()
        # Event has different game_id in payload
        event = TestEvent(event_id="999999999", value="test")

        # Pass authoritative game_id from store
        hub_with_trial_id._emit_event_span(
            event, actor_id="test_actor", sport_type="nba", game_id="401810490"
        )

        call_args = mock_create_span.call_args
        tags = call_args.kwargs["extra_tags"]
        # Store's game_id should win over event's event_id
        assert tags["game.id"] == "401810490"


class TestExtractGameId:
    """Tests for the extract_game_id utility function."""

    def test_extracts_game_id_field(self):
        """Test extraction from game_id field."""
        event_dict = {"game_id": "401810490", "event_id": "other_id"}
        assert extract_game_id(event_dict) == "401810490"

    def test_falls_back_to_event_id(self):
        """Test fallback to event_id when game_id is missing."""
        event_dict = {"event_id": "401810490"}
        assert extract_game_id(event_dict) == "401810490"

    def test_parses_pbp_event_id_format(self):
        """Test parsing of play-by-play event_id format."""
        event_dict = {"event_id": "0022400608_pbp_188"}
        assert extract_game_id(event_dict) == "0022400608"

    def test_returns_empty_string_when_no_ids(self):
        """Test returns empty string when no game_id or event_id."""
        event_dict = {"other_field": "value"}
        assert extract_game_id(event_dict) == ""

    def test_handles_non_pbp_event_id(self):
        """Test event_id that doesn't match pbp pattern is returned as-is."""
        event_dict = {"event_id": "401810490"}
        assert extract_game_id(event_dict) == "401810490"

    def test_handles_empty_game_id(self):
        """Test falls back to event_id when game_id is empty string."""
        event_dict = {"game_id": "", "event_id": "401810490"}
        assert extract_game_id(event_dict) == "401810490"


class TestDataHubLifecycleGate:
    """Tests for the per-game event lifecycle ordering gate.

    The gate enforces: GameInitializeEvent → PreGameInsight/Odds → GameStart → everything.
    Persistence and trace emission happen at delivery time (gated order), not arrival.
    """

    GAME_ID = "401810490"

    @pytest.fixture
    def gated_hub(self, temp_persistence_file):
        """Create a DataHub and patch _dispatch_event to track dispatched events."""
        hub = DataHub(
            hub_id="test_hub",
            persistence_file=temp_persistence_file,
        )
        hub._dispatched: list[DataEvent] = []  # type: ignore[attr-defined]

        original_dispatch = hub._dispatch_event

        async def tracking_dispatch(event: DataEvent) -> None:
            hub._dispatched.append(event)  # type: ignore[attr-defined]
            await original_dispatch(event)

        hub._dispatch_event = tracking_dispatch  # type: ignore[method-assign]
        return hub

    def _wrap(self, event: DataEvent) -> _EventEnvelope:
        """Wrap an event in an envelope for gate dispatch."""
        return _EventEnvelope(event=event)

    def _make_init_event(self, game_id: str | None = None) -> GameInitializeEvent:
        return GameInitializeEvent(game_id=game_id or self.GAME_ID, sport="nba")

    def _make_start_event(self, game_id: str | None = None) -> GameStartEvent:
        return GameStartEvent(game_id=game_id or self.GAME_ID, sport="nba")

    def _make_odds_event(self, game_id: str | None = None) -> OddsUpdateEvent:
        return OddsUpdateEvent(game_id=game_id or self.GAME_ID, sport="nba")

    def _make_insight_event(self, game_id: str | None = None) -> PreGameInsightEvent:
        return PreGameInsightEvent(game_id=game_id or self.GAME_ID, sport="nba")

    def _make_result_event(self, game_id: str | None = None) -> GameResultEvent:
        return GameResultEvent(
            game_id=game_id or self.GAME_ID,
            sport="nba",
            winner="home",
            home_score=110,
            away_score=98,
        )

    def _make_generic_event(self, game_id: str | None = None) -> "TestGameEvent":
        """Create a generic event that will be buffered in PREGAME."""
        return TestGameEvent(
            game_id=game_id or self.GAME_ID,
            value="generic_buffered",
        )

    def _buffered_events(self, hub: DataHub, game_id: str) -> list[DataEvent]:
        """Extract raw events from buffered envelopes."""
        return [env.event for env in hub._pending_dispatch.get(game_id, [])]

    # ----- PENDING phase -----

    @pytest.mark.asyncio
    async def test_game_init_dispatched_immediately_from_pending(self, gated_hub):
        """GameInitializeEvent should be dispatched immediately and transition to PREGAME."""
        init_event = self._make_init_event()
        await gated_hub._gated_dispatch(self._wrap(init_event))

        assert gated_hub._dispatched == [init_event]
        assert gated_hub._game_phases[self.GAME_ID] == _GamePhase.PREGAME

    @pytest.mark.asyncio
    async def test_non_init_events_buffered_in_pending(self, gated_hub):
        """Events arriving before GameInitializeEvent should be buffered."""
        start = self._make_start_event()
        odds = self._make_odds_event()
        result = self._make_result_event()

        await gated_hub._gated_dispatch(self._wrap(start))
        await gated_hub._gated_dispatch(self._wrap(odds))
        await gated_hub._gated_dispatch(self._wrap(result))

        # Nothing dispatched
        assert gated_hub._dispatched == []
        # All buffered
        assert len(gated_hub._pending_dispatch[self.GAME_ID]) == 3

    @pytest.mark.asyncio
    async def test_buffer_flushed_after_game_init(self, gated_hub):
        """Buffered events should flush in order after GameInitializeEvent arrives."""
        odds = self._make_odds_event()
        insight = self._make_insight_event()
        await gated_hub._gated_dispatch(self._wrap(odds))
        await gated_hub._gated_dispatch(self._wrap(insight))

        # Nothing dispatched yet
        assert gated_hub._dispatched == []

        # Now send GameInitializeEvent
        init_event = self._make_init_event()
        await gated_hub._gated_dispatch(self._wrap(init_event))

        # Init dispatched first, then buffered PreGameInsight and Odds (PREGAME-eligible)
        assert gated_hub._dispatched[0] == init_event
        assert odds in gated_hub._dispatched
        assert insight in gated_hub._dispatched

    # ----- PREGAME phase -----

    @pytest.mark.asyncio
    async def test_pregame_insight_dispatched_in_pregame(self, gated_hub):
        """PreGameInsightEvent should be dispatched in PREGAME phase."""
        await gated_hub._gated_dispatch(self._wrap(self._make_init_event()))
        gated_hub._dispatched.clear()

        insight = self._make_insight_event()
        await gated_hub._gated_dispatch(self._wrap(insight))

        assert gated_hub._dispatched == [insight]

    @pytest.mark.asyncio
    async def test_odds_dispatched_in_pregame(self, gated_hub):
        """OddsUpdateEvent should be dispatched in PREGAME phase."""
        await gated_hub._gated_dispatch(self._wrap(self._make_init_event()))
        gated_hub._dispatched.clear()

        odds = self._make_odds_event()
        await gated_hub._gated_dispatch(self._wrap(odds))

        assert gated_hub._dispatched == [odds]

    @pytest.mark.asyncio
    async def test_game_result_transitions_to_live_in_pregame(self, gated_hub):
        """GameResultEvent in PREGAME should transition to LIVE and dispatch.

        This handles concluded/historical games where GameStartEvent never fires.
        """
        await gated_hub._gated_dispatch(self._wrap(self._make_init_event()))
        gated_hub._dispatched.clear()

        result = self._make_result_event()
        await gated_hub._gated_dispatch(self._wrap(result))

        assert gated_hub._dispatched == [result]
        assert gated_hub._game_phases[self.GAME_ID] == _GamePhase.LIVE

    # ----- LIVE phase -----

    @pytest.mark.asyncio
    async def test_game_start_transitions_to_live(self, gated_hub):
        """GameStartEvent in PREGAME should transition to LIVE and dispatch."""
        await gated_hub._gated_dispatch(self._wrap(self._make_init_event()))
        gated_hub._dispatched.clear()

        start = self._make_start_event()
        await gated_hub._gated_dispatch(self._wrap(start))

        assert start in gated_hub._dispatched
        assert gated_hub._game_phases[self.GAME_ID] == _GamePhase.LIVE

    @pytest.mark.asyncio
    async def test_game_start_flushes_remaining_buffer(self, gated_hub):
        """GameStartEvent should flush any remaining buffered events."""
        await gated_hub._gated_dispatch(self._wrap(self._make_init_event()))

        # Buffer a generic event (not allowed in PREGAME)
        generic = self._make_generic_event()
        await gated_hub._gated_dispatch(self._wrap(generic))
        gated_hub._dispatched.clear()

        # GameStartEvent triggers LIVE + flush
        start = self._make_start_event()
        await gated_hub._gated_dispatch(self._wrap(start))

        assert gated_hub._dispatched == [start, generic]
        assert self.GAME_ID not in gated_hub._pending_dispatch

    @pytest.mark.asyncio
    async def test_everything_dispatched_in_live(self, gated_hub):
        """All events should dispatch immediately in LIVE phase."""
        # Transition to LIVE
        await gated_hub._gated_dispatch(self._wrap(self._make_init_event()))
        await gated_hub._gated_dispatch(self._wrap(self._make_start_event()))
        gated_hub._dispatched.clear()

        result = self._make_result_event()
        odds = self._make_odds_event()
        insight = self._make_insight_event()

        await gated_hub._gated_dispatch(self._wrap(result))
        await gated_hub._gated_dispatch(self._wrap(odds))
        await gated_hub._gated_dispatch(self._wrap(insight))

        assert gated_hub._dispatched == [result, odds, insight]

    # ----- No game_id (bypass gate) -----

    @pytest.mark.asyncio
    async def test_no_game_id_always_dispatched(self, gated_hub):
        """Events without game_id bypass the gate entirely."""
        event = make_test_event("no_gate")
        await gated_hub._gated_dispatch(self._wrap(event))

        assert gated_hub._dispatched == [event]

    # ----- Buffer overflow -----

    @pytest.mark.asyncio
    async def test_buffer_overflow_forces_live(self, gated_hub):
        """Buffer exceeding max_pending_per_game should force-transition to LIVE."""
        gated_hub._max_pending_per_game = 5

        # Send 6 events without GameInitializeEvent
        for _ in range(6):
            evt = GameStartEvent(game_id=self.GAME_ID, sport="nba")
            await gated_hub._gated_dispatch(self._wrap(evt))

        # Should have force-transitioned to LIVE and flushed
        assert gated_hub._game_phases[self.GAME_ID] == _GamePhase.LIVE
        assert len(gated_hub._dispatched) == 6

    # ----- Multiple games independent -----

    @pytest.mark.asyncio
    async def test_multiple_games_tracked_independently(self, gated_hub):
        """Each game_id should have its own lifecycle phase."""
        game_a = "game_a"
        game_b = "game_b"

        # Initialize game A only
        await gated_hub._gated_dispatch(self._wrap(self._make_init_event(game_a)))

        # Game A is PREGAME, game B is still PENDING
        assert gated_hub._game_phases[game_a] == _GamePhase.PREGAME
        assert (
            gated_hub._game_phases.get(game_b, _GamePhase.PENDING) == _GamePhase.PENDING
        )

        # Odds event for game A dispatches, for game B buffers
        odds_a = self._make_odds_event(game_a)
        odds_b = self._make_odds_event(game_b)
        await gated_hub._gated_dispatch(self._wrap(odds_a))
        await gated_hub._gated_dispatch(self._wrap(odds_b))

        assert odds_a in gated_hub._dispatched
        assert odds_b not in gated_hub._dispatched
        assert odds_b in self._buffered_events(gated_hub, game_b)

    # ----- Flush respects phase: GameStart in buffer triggers LIVE -----

    @pytest.mark.asyncio
    async def test_flush_handles_game_start_in_buffer(self, gated_hub):
        """If GameStartEvent is buffered in PENDING, flushing after init should
        dispatch it and transition to LIVE, flushing remaining events too."""
        # Buffer: GameStart, then a result
        start = self._make_start_event()
        result = self._make_result_event()
        await gated_hub._gated_dispatch(self._wrap(start))
        await gated_hub._gated_dispatch(self._wrap(result))

        assert gated_hub._dispatched == []

        # GameInitializeEvent arrives → PREGAME → flush buffer
        # Buffer has [GameStart, GameResult]
        # Flush: GameStart dispatched (→ LIVE), then GameResult dispatched
        init_event = self._make_init_event()
        await gated_hub._gated_dispatch(self._wrap(init_event))

        assert gated_hub._dispatched[0] == init_event
        assert start in gated_hub._dispatched
        assert result in gated_hub._dispatched
        assert gated_hub._game_phases[self.GAME_ID] == _GamePhase.LIVE
        assert self.GAME_ID not in gated_hub._pending_dispatch

    # ----- Duplicate GameInitializeEvent -----

    @pytest.mark.asyncio
    async def test_duplicate_game_init_dispatched_normally(self, gated_hub):
        """A second GameInitializeEvent for the same game dispatches without re-transitioning."""
        init1 = self._make_init_event()
        init2 = self._make_init_event()

        await gated_hub._gated_dispatch(self._wrap(init1))
        await gated_hub._gated_dispatch(self._wrap(init2))

        assert gated_hub._dispatched == [init1, init2]
        assert gated_hub._game_phases[self.GAME_ID] == _GamePhase.PREGAME

    # ----- Persistence ordering -----

    @pytest.mark.asyncio
    async def test_persistence_reflects_gated_order(self, temp_persistence_file):
        """JSONL should reflect gated lifecycle order, not raw arrival order."""
        hub = DataHub(
            hub_id="test_hub",
            persistence_file=temp_persistence_file,
        )

        # Events arrive out of order: odds, start, then init
        odds = OddsUpdateEvent(game_id=self.GAME_ID, sport="nba")
        start = GameStartEvent(game_id=self.GAME_ID, sport="nba")
        init_event = GameInitializeEvent(game_id=self.GAME_ID, sport="nba")

        await hub.receive_event(odds)
        await hub.receive_event(start)
        # Nothing persisted yet — both buffered in PENDING
        with open(temp_persistence_file) as f:
            assert f.read() == ""

        # Init arrives → persists init, then flushes odds (PREGAME-eligible),
        # then start triggers LIVE transition
        await hub.receive_event(init_event)

        with open(temp_persistence_file) as f:
            lines = f.readlines()

        event_types = [json.loads(line)["event_type"] for line in lines]
        assert event_types[0] == "event.game_initialize"
        assert "event.odds_update" in event_types
        assert "event.game_start" in event_types
        # Init is always first
        assert event_types.index("event.game_initialize") < event_types.index(
            "event.odds_update"
        )
        assert event_types.index("event.game_initialize") < event_types.index(
            "event.game_start"
        )
