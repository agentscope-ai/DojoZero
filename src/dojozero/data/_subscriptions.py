"""Subscription management for DataHub.

Extracted from DataHub to provide a clean interface for both internal
and external (HTTP/SSE) agents.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from dojozero.data._models import DataEvent

logger = logging.getLogger(__name__)


class EventPriority(IntEnum):
    """Event priority levels for backpressure handling.

    CRITICAL events (odds updates, game lifecycle) are never dropped.
    HIGH events (game state updates) are batched under pressure.
    NORMAL events (play-by-play) can be dropped under pressure.
    """

    CRITICAL = 0  # Lifecycle events, odds updates - never drop
    HIGH = 1  # Game state updates
    NORMAL = 2  # Play-by-play events


@dataclass(frozen=True)
class SubscriptionFilter:
    """Filters for subscription matching.

    Supports:
    - Exact event_type match: "event.nba_play"
    - Wildcard patterns: "event.nba_*", "event.*_update"
    """

    event_types: frozenset[str] = field(default_factory=frozenset)

    def matches(self, event_type: str) -> bool:
        """Check if an event type matches this filter."""
        if not self.event_types:
            return True  # Empty filter matches all

        for pattern in self.event_types:
            if "*" in pattern or "?" in pattern:
                if fnmatch(event_type, pattern):
                    return True
            elif event_type == pattern:
                return True
        return False

    @classmethod
    def from_list(cls, event_types: list[str] | None) -> SubscriptionFilter:
        """Create filter from list of event types/patterns."""
        if not event_types:
            return cls()
        return cls(event_types=frozenset(event_types))


@dataclass
class SubscriptionOptions:
    """Configuration options for a subscription."""

    include_snapshot: bool = True
    buffer_threshold_warn: int = 100
    buffer_threshold_batch: int = 500
    buffer_threshold_drop: int = 1000


@dataclass
class Subscription:
    """Represents an active subscription with its queue and state.

    External agents use this class to receive events via the queue.
    Internal agents continue to use the legacy callback-based API.
    """

    subscription_id: str
    subscriber_id: str
    filters: SubscriptionFilter
    options: SubscriptionOptions

    # Internal state
    _queue: asyncio.Queue["DataEvent"] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1000)
    )
    _sequence: int = 0
    _created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _last_event_at: datetime | None = None
    _dropped_count: int = 0

    def get_next_sequence(self) -> int:
        """Get and increment sequence number."""
        self._sequence += 1
        return self._sequence

    @property
    def sequence(self) -> int:
        """Current sequence number (without incrementing)."""
        return self._sequence

    @property
    def buffer_depth(self) -> int:
        """Current number of buffered events."""
        return self._queue.qsize()

    @property
    def dropped_count(self) -> int:
        """Number of events dropped due to backpressure."""
        return self._dropped_count

    async def put(
        self,
        event: "DataEvent",
        priority: EventPriority = EventPriority.NORMAL,
    ) -> bool:
        """Add event to queue with backpressure handling.

        Args:
            event: Event to add
            priority: Event priority for backpressure decisions

        Returns:
            True if event was queued, False if dropped
        """
        if not self.filters.matches(event.event_type):
            return False

        depth = self.buffer_depth

        # Never drop critical events
        if priority == EventPriority.CRITICAL:
            try:
                self._queue.put_nowait(event)
                self._last_event_at = datetime.now(timezone.utc)
                return True
            except asyncio.QueueFull:
                # Force-add critical events by removing oldest
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(event)
                    self._last_event_at = datetime.now(timezone.utc)
                    return True
                except Exception:
                    return False

        # Handle backpressure for non-critical events
        if depth > self.options.buffer_threshold_drop:
            self._dropped_count += 1
            if self._dropped_count % 100 == 1:
                logger.warning(
                    "Subscription %s: dropped %d events (buffer depth: %d)",
                    self.subscription_id,
                    self._dropped_count,
                    depth,
                )
            return False

        try:
            self._queue.put_nowait(event)
            self._last_event_at = datetime.now(timezone.utc)
            return True
        except asyncio.QueueFull:
            self._dropped_count += 1
            return False

    async def get(self, timeout: float | None = None) -> "DataEvent | None":
        """Get next event from queue.

        Args:
            timeout: Maximum time to wait in seconds, None for indefinite

        Returns:
            Next event or None if timeout
        """
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return await self._queue.get()
        except asyncio.TimeoutError:
            return None

    def get_nowait(self) -> "DataEvent | None":
        """Non-blocking get."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def drain(self, max_count: int = 100) -> list["DataEvent"]:
        """Drain up to max_count events from queue.

        Args:
            max_count: Maximum events to drain

        Returns:
            List of drained events
        """
        events = []
        for _ in range(max_count):
            event = self.get_nowait()
            if event is None:
                break
            events.append(event)
        return events


class SubscriptionManager:
    """Manages event subscriptions with filtering, buffering, and backpressure.

    This class is the central subscription infrastructure used by both:
    - Internal agents (via legacy callback API for backward compatibility)
    - External agents (via Subscription queues for HTTP/SSE delivery)

    Thread-safe for concurrent access from multiple agents and the event
    dispatch loop.
    """

    def __init__(
        self,
        max_recent_events_per_type: int = 100,
    ):
        """Initialize SubscriptionManager.

        Args:
            max_recent_events_per_type: Max events to cache per event type
        """
        self._subscriptions: dict[str, Subscription] = {}
        self._subscriber_subscriptions: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

        # Recent events cache for late-joining subscribers
        self._recent_events: dict[str, list["DataEvent"]] = defaultdict(list)
        self._max_recent_events_per_type = max_recent_events_per_type

        # Legacy callback support (for backward compatibility with DataHub.subscribe_agent)
        self._event_handlers: dict[str, list[Callable[["DataEvent"], None]]] = (
            defaultdict(list)
        )

        # Track callbacks by subscriber for proper cleanup
        self._subscriber_callbacks: dict[
            str, list[tuple[str, Callable[["DataEvent"], None]]]
        ] = defaultdict(list)

        # Sequence tracking
        self._global_sequence: int = 0

        logger.info("SubscriptionManager initialized")

    async def subscribe(
        self,
        subscriber_id: str,
        filters: SubscriptionFilter | None = None,
        options: SubscriptionOptions | None = None,
    ) -> Subscription:
        """Create a new subscription.

        Args:
            subscriber_id: Unique identifier for the subscriber (agent_id)
            filters: Event type filters
            options: Subscription configuration

        Returns:
            Subscription object for receiving events
        """
        async with self._lock:
            subscription_id = f"{subscriber_id}_{uuid.uuid4().hex[:8]}"

            subscription = Subscription(
                subscription_id=subscription_id,
                subscriber_id=subscriber_id,
                filters=filters or SubscriptionFilter(),
                options=options or SubscriptionOptions(),
            )

            self._subscriptions[subscription_id] = subscription
            self._subscriber_subscriptions[subscriber_id].add(subscription_id)

            logger.info(
                "Created subscription: subscription_id=%s, subscriber_id=%s, filters=%s",
                subscription_id,
                subscriber_id,
                filters,
            )

            return subscription

    async def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription.

        Args:
            subscription_id: ID of subscription to remove

        Returns:
            True if subscription was found and removed
        """
        async with self._lock:
            subscription = self._subscriptions.pop(subscription_id, None)
            if subscription is None:
                return False

            self._subscriber_subscriptions[subscription.subscriber_id].discard(
                subscription_id
            )
            if not self._subscriber_subscriptions[subscription.subscriber_id]:
                del self._subscriber_subscriptions[subscription.subscriber_id]

            logger.info("Removed subscription: %s", subscription_id)
            return True

    async def unsubscribe_all(self, subscriber_id: str) -> int:
        """Remove all subscriptions for a subscriber.

        This also cleans up legacy callbacks, fixing the bug in the original
        DataHub.unsubscribe_agent() which didn't clean up callbacks.

        Args:
            subscriber_id: Subscriber whose subscriptions to remove

        Returns:
            Number of subscriptions removed
        """
        async with self._lock:
            subscription_ids = list(
                self._subscriber_subscriptions.get(subscriber_id, set())
            )

            for sub_id in subscription_ids:
                self._subscriptions.pop(sub_id, None)

            if subscriber_id in self._subscriber_subscriptions:
                del self._subscriber_subscriptions[subscriber_id]

            # Clean up legacy callbacks (fixes the bug!)
            callbacks_to_remove = self._subscriber_callbacks.pop(subscriber_id, [])
            for event_type, callback in callbacks_to_remove:
                if event_type in self._event_handlers:
                    try:
                        self._event_handlers[event_type].remove(callback)
                    except ValueError:
                        pass  # Already removed

            logger.info(
                "Removed %d subscriptions and %d callbacks for subscriber: %s",
                len(subscription_ids),
                len(callbacks_to_remove),
                subscriber_id,
            )
            return len(subscription_ids)

    async def dispatch(
        self,
        event: "DataEvent",
        priority: EventPriority = EventPriority.NORMAL,
    ) -> int:
        """Dispatch an event to all matching subscriptions.

        Args:
            event: Event to dispatch
            priority: Event priority for backpressure handling

        Returns:
            Number of subscriptions that received the event
        """
        # Cache for late joiners
        self._cache_event(event)

        # Increment global sequence
        self._global_sequence += 1

        delivered = 0
        event_type = event.event_type

        # Dispatch to Subscription queues (new pattern for external agents)
        async with self._lock:
            for subscription in self._subscriptions.values():
                if await subscription.put(event, priority):
                    delivered += 1

        # Dispatch to legacy callbacks (backward compatibility)
        if event_type in self._event_handlers:
            for handler in self._event_handlers[event_type]:
                try:
                    handler(event)
                except Exception as e:
                    logger.error("Error in event handler for %s: %s", event_type, e)

        return delivered

    def _cache_event(self, event: "DataEvent") -> None:
        """Cache event for late-joining subscribers."""
        event_type = event.event_type
        events_list = self._recent_events[event_type]
        events_list.insert(0, event)  # Newest first

        if len(events_list) > self._max_recent_events_per_type:
            self._recent_events[event_type] = events_list[
                : self._max_recent_events_per_type
            ]

    def get_recent_events(
        self,
        event_types: list[str] | None = None,
        limit: int = 10,
    ) -> list["DataEvent"]:
        """Get recent events from cache.

        Args:
            event_types: Filter by event types (None = all)
            limit: Maximum number of events to return

        Returns:
            List of recent events (newest first)
        """
        if event_types is None:
            all_events: list["DataEvent"] = []
            for events_list in self._recent_events.values():
                all_events.extend(events_list)
            all_events.sort(key=lambda e: e.timestamp, reverse=True)
            return all_events[:limit]
        else:
            result: list["DataEvent"] = []
            for event_type in event_types:
                if event_type in self._recent_events:
                    result.extend(self._recent_events[event_type])
            result.sort(key=lambda e: e.timestamp, reverse=True)
            return result[:limit]

    @property
    def global_sequence(self) -> int:
        """Current global sequence number."""
        return self._global_sequence

    # =========================================================================
    # Legacy API (backward compatibility with DataHub.subscribe_agent)
    # =========================================================================

    def subscribe_agent_legacy(
        self,
        agent_id: str,
        stream_ids: list[str] | None = None,  # noqa: ARG002 - kept for API compat
        event_types: list[str] | None = None,
        callback: Callable[["DataEvent"], None] | None = None,
    ) -> None:
        """Legacy subscribe method for backward compatibility.

        This method maintains the exact interface of DataHub.subscribe_agent()
        so existing code continues to work unchanged.

        Args:
            agent_id: Agent identifier
            stream_ids: Legacy parameter, not used (kept for API compatibility)
            event_types: Event types to subscribe to
            callback: Callback function to receive events

        Note: New code should use subscribe() instead for better control.
        """
        # stream_ids is kept for API compatibility but not used in the new system
        _ = stream_ids

        if callback and event_types:
            for event_type in event_types:
                self._event_handlers[event_type].append(callback)
                # Track for cleanup on unsubscribe
                self._subscriber_callbacks[agent_id].append((event_type, callback))

        logger.debug(
            "Legacy subscription: agent_id=%s, event_types=%s",
            agent_id,
            event_types,
        )

    def dispatch_sync(self, event: "DataEvent") -> None:
        """Synchronous dispatch to legacy callbacks only.

        Used by DataHub._dispatch_event() for backward compatibility.
        Does NOT dispatch to Subscription queues (those are handled async).
        """
        event_type = event.event_type

        if event_type in self._event_handlers:
            for handler in self._event_handlers[event_type]:
                try:
                    handler(event)
                except Exception as e:
                    logger.error("Error in event handler for %s: %s", event_type, e)


__all__ = [
    "EventPriority",
    "Subscription",
    "SubscriptionFilter",
    "SubscriptionManager",
    "SubscriptionOptions",
]
