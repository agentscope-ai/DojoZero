"""Tests for DataHub - the central event bus."""

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dojozero.data._hub import DataHub
from dojozero.data._models import DataEvent, extract_game_id, register_event


# Test event class - must be a frozen dataclass with kw_only=True for registration
@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class TestEvent(DataEvent):
    """Simple event for testing."""

    __test__ = False  # Prevent pytest from collecting this as a test class

    # timestamp is inherited from DataEvent
    event_id: str = ""
    value: str = ""

    @property
    def event_type(self) -> str:
        return "test_event"


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class TestEventWithGameTime(DataEvent):
    """Event with game_time field for testing game_date extraction."""

    __test__ = False

    event_id: str = ""
    game_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def event_type(self) -> str:
        return "test_event_with_game_time"


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class TestEventWithGameTimeUtc(DataEvent):
    """Event with game_time_utc string field for testing fallback."""

    __test__ = False

    event_id: str = ""
    game_time_utc: str = ""

    @property
    def event_type(self) -> str:
        return "test_event_with_game_time_utc"


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class TestEventWithGameTimeStr(DataEvent):
    """Event with game_time as string field for testing ISO string extraction."""

    __test__ = False

    event_id: str = ""
    game_time: str = ""

    @property
    def event_type(self) -> str:
        return "test_event_game_time_str"


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
        # First persist some events
        for i in range(3):
            event = make_test_event(f"backtest_{i}")
            await hub.receive_event(event)

        # Create new hub and start backtest (uses same file for consistency)
        backtest_hub = DataHub(
            hub_id="backtest_hub", persistence_file=temp_persistence_file
        )
        await backtest_hub.start_backtest(temp_persistence_file)

        assert len(backtest_hub._backtest_events) == 3
        assert backtest_hub._backtest_mode is True

    @pytest.mark.asyncio
    async def test_backtest_next_returns_events_in_order(
        self, hub, temp_persistence_file
    ):
        """Test that backtest_next returns events in order."""
        # Persist events
        for i in range(3):
            event = make_test_event(f"order_{i}")
            await hub.receive_event(event)

        # Backtest (uses same file for consistency)
        backtest_hub = DataHub(
            hub_id="backtest_hub", persistence_file=temp_persistence_file
        )
        await backtest_hub.start_backtest(temp_persistence_file)

        event1 = await backtest_hub.backtest_next()
        event2 = await backtest_hub.backtest_next()
        event3 = await backtest_hub.backtest_next()
        event4 = await backtest_hub.backtest_next()

        assert isinstance(event1, TestEvent) and event1.value == "order_0"
        assert isinstance(event2, TestEvent) and event2.value == "order_1"
        assert isinstance(event3, TestEvent) and event3.value == "order_2"
        assert event4 is None  # No more events

    @pytest.mark.asyncio
    async def test_backtest_all_dispatches_all_events(self, hub, temp_persistence_file):
        """Test that backtest_all dispatches all events."""
        # Persist events
        for i in range(3):
            event = make_test_event(f"all_{i}")
            await hub.receive_event(event)

        # Setup backtest hub with callback (uses same file for consistency)
        backtest_hub = DataHub(
            hub_id="backtest_hub", persistence_file=temp_persistence_file
        )
        callback = MagicMock()
        backtest_hub.subscribe_agent(
            agent_id="agent1", event_types=["test_event"], callback=callback
        )

        await backtest_hub.start_backtest(temp_persistence_file)
        await backtest_hub.backtest_all()

        assert callback.call_count == 3

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
        """Test that connecting a store sets its event emitter."""
        mock_store = MagicMock()
        mock_store._data_hub = None

        hub.connect_store(mock_store)

        mock_store.set_event_emitter.assert_called_once()
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
