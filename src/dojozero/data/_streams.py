"""Generic DataStream that bridges DataHub events to StreamEvent system."""

import asyncio
import logging
from typing import Any, Mapping, Protocol, TypedDict

from dojozero.core import DataStream, DataStreamBase, StreamEvent
from dojozero.data import DataHub
from dojozero.data._models import DataEvent

logger = logging.getLogger(__name__)


class _ActorIdConfig(TypedDict):
    actor_id: str


class DataHubDataStreamConfig(_ActorIdConfig, total=False):
    """Configuration for DataHubDataStream."""

    hub_id: str
    persistence_file: str
    event_type: str  # Which event_type to subscribe to in DataHub
    event_types: list[
        str
    ]  # Which event_types to subscribe to (alternative to event_type)


class StreamInitializer(Protocol):
    """Protocol for stream initialization logic.

    Implementations can trigger initial events, set up subscriptions, etc.
    """

    async def initialize(self, stream: "DataHubDataStream") -> None:
        """Initialize the stream (e.g., trigger initial events).

        Args:
            stream: The DataHubDataStream instance to initialize
        """
        ...


class DataHubDataStream(DataStreamBase, DataStream[DataHubDataStreamConfig]):
    """Generic DataStream that bridges DataHub events to StreamEvent system.

    Subscribes to DataHub events and converts them to StreamEvent[DataEvent]
    for consumption by agents. This is a generic implementation that can be
    extended for domain-specific initialization logic.
    """

    def __init__(
        self,
        *,
        actor_id: str,
        hub: DataHub | None = None,
        event_type: str | None = None,
        event_types: list[str] | None = None,
        initializer: StreamInitializer | None = None,
    ) -> None:
        super().__init__(actor_id)
        self._hub = hub
        self._event_type = event_type
        self._event_types = event_types or []
        self._initializer = initializer
        self._sequence = 0
        self._received_events: list[DataEvent] = []
        self._initialized = False

    @classmethod
    def from_dict(
        cls,
        config: DataHubDataStreamConfig,
        context: dict[str, Any] | None = None,
    ) -> "DataHubDataStream":
        # Get hub from context (provided by dashboard during materialization)
        hub: DataHub | None = None

        if context and "data_hubs" in context:
            hub_id = config.get("hub_id", "default_hub")
            hub = context["data_hubs"].get(hub_id)

        if hub is None:
            # Fallback: create new hub (shouldn't happen in normal flow)
            hub_id = config.get("hub_id", "default_hub")
            persistence_file = config.get("persistence_file", "outputs/events.jsonl")
            hub = DataHub(hub_id=hub_id, persistence_file=persistence_file)

        return cls(
            actor_id=config["actor_id"],
            hub=hub,
            event_type=config.get("event_type"),
            event_types=config.get("event_types", []),
        )

    async def start(self) -> None:
        """Protocol hook: subscribe to DataHub events and run initializer if provided."""
        if self._hub is None:
            raise RuntimeError(f"stream '{self.actor_id}' has no DataHub instance")

        logger.info(
            "stream '%s' starting: event_type=%s event_types=%s",
            self.actor_id,
            self._event_type,
            self._event_types,
        )

        # Subscribe to DataHub events using callback mechanism FIRST
        # This ensures we're subscribed before any events are emitted
        def event_callback(event: DataEvent) -> None:
            # Check if this event matches our subscription
            should_forward = False
            if self._event_type and event.event_type == self._event_type:
                should_forward = True
            elif self._event_types and event.event_type in self._event_types:
                should_forward = True

            if should_forward:
                self._received_events.append(event)
                # Schedule async publish
                asyncio.create_task(self._publish_event(event))

        # Determine event types to subscribe to
        subscribe_event_types = self._event_types.copy()
        if self._event_type and self._event_type not in subscribe_event_types:
            subscribe_event_types.append(self._event_type)

        # Subscribe to DataHub using subscribe_agent mechanism
        # We use the stream's actor_id as the agent_id for subscription
        # DataHub stores the callback internally, so we don't need to keep a reference
        for event_type in subscribe_event_types:
            self._hub.subscribe_agent(
                agent_id=self.actor_id,
                event_types=[event_type],
                callback=event_callback,
            )

        # Run initializer if provided (e.g., trigger initial searches)
        # Do this AFTER subscribing so events aren't missed, but run it in background
        # so it doesn't block pipeline setup
        if self._initializer and not self._initialized:
            self._initialized = True
            # Schedule initializer to run in background - don't await it
            # This allows pipeline setup to complete while searches happen asynchronously
            asyncio.create_task(self._run_initializer())

    async def _run_initializer(self) -> None:
        """Run the initializer in the background without blocking."""
        if self._initializer:
            try:
                await self._initializer.initialize(self)
            except Exception as e:
                logger.error(
                    "Initializer failed for stream '%s': %s",
                    self.actor_id,
                    e,
                    exc_info=True,
                )

    async def _publish_event(self, event: DataEvent) -> None:
        """Publish a DataEvent as a StreamEvent."""
        self._sequence += 1
        stream_event = StreamEvent(
            stream_id=self.actor_id,
            payload=event,
            emitted_at=event.timestamp,
            sequence=self._sequence,
        )
        await self._publish(stream_event)

    async def stop(self) -> None:
        """Protocol hook: unsubscribe from DataHub."""
        logger.info(
            "stream '%s' stopping after %d events",
            self.actor_id,
            len(self._received_events),
        )
        # Unsubscribe from DataHub
        if self._hub:
            self._hub.unsubscribe_agent(self.actor_id)

    async def save_state(self) -> Mapping[str, Any]:
        """Protocol hook: dashboard snapshot for checkpoints."""
        return {
            "sequence": self._sequence,
            "received_events_count": len(self._received_events),
            "initialized": self._initialized,
            # Include full event history in chronological order
            "events": [event.to_dict() for event in self._received_events],
        }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Protocol hook: dashboard restores a checkpoint before resuming."""
        from dojozero.data._models import DataEventFactory

        self._sequence = int(state.get("sequence", 0))
        self._initialized = bool(state.get("initialized", False))

        # Restore events from checkpoint (if present)
        events_data = state.get("events", [])
        self._received_events = []
        for event_dict in events_data:
            try:
                event = DataEventFactory.from_dict(event_dict)
                if event is not None:
                    self._received_events.append(event)
            except Exception as e:
                logger.warning(
                    "stream '%s' failed to restore event: %s",
                    self.actor_id,
                    e,
                )

        logger.info(
            "stream '%s' restored: sequence=%d initialized=%s events=%d",
            self.actor_id,
            self._sequence,
            self._initialized,
            len(self._received_events),
        )

    @property
    def hub(self) -> DataHub:
        """Get the underlying DataHub instance."""
        if self._hub is None:
            raise RuntimeError(f"stream '{self.actor_id}' has no DataHub instance")
        return self._hub
