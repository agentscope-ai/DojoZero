"""Tests for DataHub - the central event bus."""

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dojozero.data._hub import DataHub
from dojozero.data._models import DataEvent, register_event


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


@pytest.fixture
def temp_persistence_file():
    """Create a temporary file for event persistence."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        yield Path(f.name)
    # Cleanup handled by tempfile


@pytest.fixture
def hub(temp_persistence_file):
    """Create a DataHub instance with persistence enabled."""
    return DataHub(
        hub_id="test_hub",
        persistence_file=temp_persistence_file,
        enable_persistence=True,
    )


@pytest.fixture
def hub_no_persistence():
    """Create a DataHub instance without persistence."""
    return DataHub(
        hub_id="test_hub_no_persist",
        enable_persistence=False,
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

    @pytest.mark.asyncio
    async def test_persistence_disabled(self, hub_no_persistence, tmp_path):
        """Test that events are not persisted when disabled."""
        # Set persistence file to a temp location to verify nothing is written
        test_file = tmp_path / "test_no_persist.jsonl"
        hub_no_persistence.persistence_file = test_file

        event = make_test_event("no_persist")
        await hub_no_persistence.receive_event(event)

        # File should not exist since persistence is disabled
        assert not test_file.exists()


class TestDataHubReplay:
    """Tests for replay mode."""

    @pytest.mark.asyncio
    async def test_start_replay_loads_events(self, hub, temp_persistence_file):
        """Test that start_replay loads events from file."""
        # First persist some events
        for i in range(3):
            event = make_test_event(f"replay_{i}")
            await hub.receive_event(event)

        # Create new hub and start replay
        replay_hub = DataHub(hub_id="replay_hub", enable_persistence=False)
        await replay_hub.start_replay(temp_persistence_file)

        assert len(replay_hub._replay_events) == 3
        assert replay_hub._replay_mode is True

    @pytest.mark.asyncio
    async def test_replay_next_returns_events_in_order(
        self, hub, temp_persistence_file
    ):
        """Test that replay_next returns events in order."""
        # Persist events
        for i in range(3):
            event = make_test_event(f"order_{i}")
            await hub.receive_event(event)

        # Replay
        replay_hub = DataHub(hub_id="replay_hub", enable_persistence=False)
        await replay_hub.start_replay(temp_persistence_file)

        event1 = await replay_hub.replay_next()
        event2 = await replay_hub.replay_next()
        event3 = await replay_hub.replay_next()
        event4 = await replay_hub.replay_next()

        assert isinstance(event1, TestEvent) and event1.value == "order_0"
        assert isinstance(event2, TestEvent) and event2.value == "order_1"
        assert isinstance(event3, TestEvent) and event3.value == "order_2"
        assert event4 is None  # No more events

    @pytest.mark.asyncio
    async def test_replay_all_dispatches_all_events(self, hub, temp_persistence_file):
        """Test that replay_all dispatches all events."""
        # Persist events
        for i in range(3):
            event = make_test_event(f"all_{i}")
            await hub.receive_event(event)

        # Setup replay hub with callback
        replay_hub = DataHub(hub_id="replay_hub", enable_persistence=False)
        callback = MagicMock()
        replay_hub.subscribe_agent(
            agent_id="agent1", event_types=["test_event"], callback=callback
        )

        await replay_hub.start_replay(temp_persistence_file)
        await replay_hub.replay_all()

        assert callback.call_count == 3

    @pytest.mark.asyncio
    async def test_stop_replay_clears_state(self, hub, temp_persistence_file):
        """Test that stop_replay clears replay state."""
        # Persist event
        await hub.receive_event(make_test_event("stop_test"))

        # Start and stop replay
        replay_hub = DataHub(hub_id="replay_hub", enable_persistence=False)
        await replay_hub.start_replay(temp_persistence_file)
        replay_hub.stop_replay()

        assert replay_hub._replay_mode is False
        assert len(replay_hub._replay_events) == 0
        assert replay_hub._replay_index == 0

    @pytest.mark.asyncio
    async def test_replay_missing_file_raises(self, hub):
        """Test that replaying from missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            await hub.start_replay("/nonexistent/file.jsonl")


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
