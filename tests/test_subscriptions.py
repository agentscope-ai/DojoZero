"""Tests for SubscriptionManager."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from dojozero.data._subscriptions import (
    EventPriority,
    Subscription,
    SubscriptionFilter,
    SubscriptionManager,
    SubscriptionOptions,
)


class MockDataEvent:
    """Mock DataEvent for testing."""

    def __init__(self, event_type: str, timestamp: datetime | None = None):
        self.event_type = event_type
        self.timestamp = timestamp or datetime.now(timezone.utc)


class TestSubscriptionFilter:
    """Tests for SubscriptionFilter."""

    def test_empty_filter_matches_all(self):
        """Empty filter should match all event types."""
        f = SubscriptionFilter()
        assert f.matches("event.nba_play")
        assert f.matches("event.odds_update")
        assert f.matches("anything")

    def test_exact_match(self):
        """Filter should match exact event types."""
        f = SubscriptionFilter(event_types=frozenset(["event.nba_play"]))
        assert f.matches("event.nba_play")
        assert not f.matches("event.nba_game_update")
        assert not f.matches("event.odds_update")

    def test_wildcard_match(self):
        """Filter should support wildcard patterns."""
        f = SubscriptionFilter(event_types=frozenset(["event.nba_*"]))
        assert f.matches("event.nba_play")
        assert f.matches("event.nba_game_update")
        assert not f.matches("event.nfl_play")
        assert not f.matches("event.odds_update")

    def test_multiple_patterns(self):
        """Filter should match any of multiple patterns."""
        f = SubscriptionFilter(
            event_types=frozenset(["event.nba_*", "event.odds_update"])
        )
        assert f.matches("event.nba_play")
        assert f.matches("event.odds_update")
        assert not f.matches("event.nfl_play")

    def test_from_list(self):
        """from_list should create filter from list."""
        f = SubscriptionFilter.from_list(["event.nba_*", "event.odds_*"])
        assert f.matches("event.nba_play")
        assert f.matches("event.odds_update")
        assert not f.matches("event.nfl_play")

    def test_from_list_none(self):
        """from_list with None should create empty filter."""
        f = SubscriptionFilter.from_list(None)
        assert f.matches("anything")


class TestSubscription:
    """Tests for Subscription class."""

    @pytest.fixture
    def subscription(self):
        """Create a test subscription."""
        return Subscription(
            subscription_id="test_sub",
            subscriber_id="agent1",
            filters=SubscriptionFilter.from_list(["event.*"]),
            options=SubscriptionOptions(
                buffer_threshold_drop=10,  # Low threshold for testing
            ),
        )

    @pytest.mark.asyncio
    async def test_put_and_get(self, subscription):
        """Test basic put and get operations."""
        event = MockDataEvent("event.test")

        result = await subscription.put(event)
        assert result is True
        assert subscription.buffer_depth == 1

        retrieved = await subscription.get(timeout=1.0)
        assert retrieved is event
        assert subscription.buffer_depth == 0

    @pytest.mark.asyncio
    async def test_filter_rejects_non_matching(self, subscription):
        """Events not matching filter should be rejected."""
        subscription.filters = SubscriptionFilter.from_list(["event.nba_*"])
        event = MockDataEvent("event.nfl_play")

        result = await subscription.put(event)
        assert result is False
        assert subscription.buffer_depth == 0

    @pytest.mark.asyncio
    async def test_backpressure_drops_normal_events(self, subscription):
        """Normal events should be dropped when buffer exceeds threshold."""
        # Fill buffer beyond threshold
        for i in range(15):
            event = MockDataEvent(f"event.test_{i}")
            await subscription.put(event, priority=EventPriority.NORMAL)

        # Some events should have been dropped
        assert subscription.dropped_count > 0

    @pytest.mark.asyncio
    async def test_critical_events_never_dropped(self, subscription):
        """Critical events should never be dropped."""
        # Fill buffer
        for i in range(15):
            event = MockDataEvent(f"event.test_{i}")
            await subscription.put(event, priority=EventPriority.NORMAL)

        initial_dropped = subscription.dropped_count

        # Add critical event
        critical_event = MockDataEvent("event.critical")
        result = await subscription.put(critical_event, priority=EventPriority.CRITICAL)
        assert result is True

        # Critical event should be in queue (dropped count shouldn't increase for it)
        assert subscription.dropped_count == initial_dropped

    @pytest.mark.asyncio
    async def test_get_timeout(self, subscription):
        """get should return None on timeout."""
        result = await subscription.get(timeout=0.1)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_nowait(self, subscription):
        """get_nowait should return None when empty."""
        result = subscription.get_nowait()
        assert result is None

        event = MockDataEvent("event.test")
        await subscription.put(event)

        result = subscription.get_nowait()
        assert result is event

    @pytest.mark.asyncio
    async def test_drain(self, subscription):
        """drain should return multiple events."""
        for i in range(5):
            await subscription.put(MockDataEvent(f"event.test_{i}"))

        events = await subscription.drain(max_count=3)
        assert len(events) == 3
        assert subscription.buffer_depth == 2

    def test_sequence_tracking(self, subscription):
        """Sequence should increment on each call."""
        assert subscription.sequence == 0

        seq1 = subscription.get_next_sequence()
        assert seq1 == 1
        assert subscription.sequence == 1

        seq2 = subscription.get_next_sequence()
        assert seq2 == 2


class TestSubscriptionManager:
    """Tests for SubscriptionManager."""

    @pytest.fixture
    def manager(self):
        """Create a test subscription manager."""
        return SubscriptionManager(max_recent_events_per_type=10)

    @pytest.mark.asyncio
    async def test_subscribe(self, manager):
        """Test creating a subscription."""
        sub = await manager.subscribe(
            subscriber_id="agent1",
            filters=SubscriptionFilter.from_list(["event.nba_*"]),
        )

        assert sub.subscriber_id == "agent1"
        assert sub.subscription_id.startswith("agent1_")
        assert "agent1" in manager._subscriber_subscriptions

    @pytest.mark.asyncio
    async def test_unsubscribe(self, manager):
        """Test removing a subscription."""
        sub = await manager.subscribe(subscriber_id="agent1")

        result = await manager.unsubscribe(sub.subscription_id)
        assert result is True

        result = await manager.unsubscribe(sub.subscription_id)
        assert result is False  # Already removed

    @pytest.mark.asyncio
    async def test_unsubscribe_all(self, manager):
        """Test removing all subscriptions for a subscriber."""
        await manager.subscribe(subscriber_id="agent1")
        await manager.subscribe(subscriber_id="agent1")
        await manager.subscribe(subscriber_id="agent2")

        count = await manager.unsubscribe_all("agent1")
        assert count == 2

        assert "agent1" not in manager._subscriber_subscriptions
        assert "agent2" in manager._subscriber_subscriptions

    @pytest.mark.asyncio
    async def test_dispatch(self, manager):
        """Test dispatching events to subscriptions."""
        sub1 = await manager.subscribe(
            subscriber_id="agent1",
            filters=SubscriptionFilter.from_list(["event.nba_*"]),
        )
        sub2 = await manager.subscribe(
            subscriber_id="agent2",
            filters=SubscriptionFilter.from_list(["event.nfl_*"]),
        )

        event = MockDataEvent("event.nba_play")
        delivered = await manager.dispatch(event)

        assert delivered == 1  # Only sub1 should receive
        assert sub1.buffer_depth == 1
        assert sub2.buffer_depth == 0

    @pytest.mark.asyncio
    async def test_dispatch_to_multiple(self, manager):
        """Test dispatching to multiple matching subscriptions."""
        await manager.subscribe(
            subscriber_id="agent1",
            filters=SubscriptionFilter.from_list(["event.*"]),
        )
        await manager.subscribe(
            subscriber_id="agent2",
            filters=SubscriptionFilter.from_list(["event.*"]),
        )

        event = MockDataEvent("event.test")
        delivered = await manager.dispatch(event)

        assert delivered == 2

    @pytest.mark.asyncio
    async def test_global_sequence(self, manager):
        """Test global sequence tracking."""
        assert manager.global_sequence == 0

        await manager.dispatch(MockDataEvent("event.test"))
        assert manager.global_sequence == 1

        await manager.dispatch(MockDataEvent("event.test"))
        assert manager.global_sequence == 2

    @pytest.mark.asyncio
    async def test_recent_events_cache(self, manager):
        """Test recent events caching."""
        for i in range(5):
            event = MockDataEvent(f"event.type_{i % 2}")
            await manager.dispatch(event)

        # Get all recent events
        events = manager.get_recent_events(limit=10)
        assert len(events) == 5

        # Get filtered events
        events = manager.get_recent_events(event_types=["event.type_0"], limit=10)
        assert len(events) == 3  # 0, 2, 4

    @pytest.mark.asyncio
    async def test_recent_events_limit(self, manager):
        """Test recent events respects max limit."""
        manager._max_recent_events_per_type = 3

        for i in range(10):
            await manager.dispatch(MockDataEvent("event.test"))

        events = manager.get_recent_events(event_types=["event.test"], limit=100)
        assert len(events) == 3  # Limited by max_recent_events_per_type

    def test_legacy_subscribe_agent(self, manager):
        """Test legacy subscribe_agent_legacy method."""
        callback = MagicMock()

        manager.subscribe_agent_legacy(
            agent_id="agent1",
            event_types=["event.test"],
            callback=callback,
        )

        assert "event.test" in manager._event_handlers
        assert callback in manager._event_handlers["event.test"]

    @pytest.mark.asyncio
    async def test_legacy_callback_cleanup(self, manager):
        """Test that unsubscribe_all cleans up legacy callbacks."""
        callback = MagicMock()

        manager.subscribe_agent_legacy(
            agent_id="agent1",
            event_types=["event.test"],
            callback=callback,
        )

        assert callback in manager._event_handlers["event.test"]

        await manager.unsubscribe_all("agent1")

        assert callback not in manager._event_handlers["event.test"]

    def test_dispatch_sync(self, manager):
        """Test synchronous dispatch to callbacks."""
        callback = MagicMock()

        manager.subscribe_agent_legacy(
            agent_id="agent1",
            event_types=["event.test"],
            callback=callback,
        )

        event = MockDataEvent("event.test")
        manager.dispatch_sync(event)

        callback.assert_called_once_with(event)

    def test_dispatch_sync_error_handling(self, manager):
        """Test that callback errors don't break dispatch."""
        callback1 = MagicMock(side_effect=Exception("test error"))
        callback2 = MagicMock()

        manager.subscribe_agent_legacy(
            agent_id="agent1",
            event_types=["event.test"],
            callback=callback1,
        )
        manager.subscribe_agent_legacy(
            agent_id="agent2",
            event_types=["event.test"],
            callback=callback2,
        )

        event = MockDataEvent("event.test")
        manager.dispatch_sync(event)

        # Both callbacks should be called, despite first one raising
        callback1.assert_called_once()
        callback2.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_sync_delivers_to_subscriptions(self, manager):
        """Test that dispatch_sync also delivers to subscription queues.

        This is critical for external agents using SSE/REST polling.
        """
        # Create a subscription (like an external agent would)
        sub = await manager.subscribe(
            subscriber_id="external_agent",
            filters=SubscriptionFilter.from_list(["event.*"]),
        )

        # Dispatch via sync method (like DataHub._dispatch_event does)
        event = MockDataEvent("event.test")
        delivered = manager.dispatch_sync(event)

        # Event should be delivered to the subscription queue
        assert delivered == 1
        assert sub.buffer_depth == 1

        # Should be retrievable from the queue
        received = sub.get_nowait()
        assert received is event

    @pytest.mark.asyncio
    async def test_dispatch_sync_updates_sequence(self, manager):
        """Test that dispatch_sync increments global sequence."""
        assert manager.global_sequence == 0

        manager.dispatch_sync(MockDataEvent("event.test"))
        assert manager.global_sequence == 1

        manager.dispatch_sync(MockDataEvent("event.test"))
        assert manager.global_sequence == 2

    @pytest.mark.asyncio
    async def test_dispatch_sync_caches_events(self, manager):
        """Test that dispatch_sync caches events for late joiners."""
        # Dispatch some events
        manager.dispatch_sync(MockDataEvent("event.type_a"))
        manager.dispatch_sync(MockDataEvent("event.type_b"))

        # Should be cached
        events = manager.get_recent_events(limit=10)
        assert len(events) == 2
