"""Generic DataStream that bridges DataHub events to StreamEvent system."""

import asyncio
import logging
from typing import Any, Mapping, Protocol, TypedDict

from agentx.core import DataStream, DataStreamBase, StreamEvent
from agentx.data import DataHub
from agentx.data._models import DataEvent

LOGGER = logging.getLogger("agentx.data.streams")


class _ActorIdConfig(TypedDict):
    actor_id: str


class DataHubDataStreamConfig(_ActorIdConfig, total=False):
    """Configuration for DataHubDataStream."""
    hub_id: str
    persistence_file: str
    stream_id: str  # Which stream_id to subscribe to in DataHub
    event_types: list[str]  # Which event_types to subscribe to (alternative to stream_id)


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


class DataHubDataStream(
    DataStreamBase, DataStream[DataHubDataStreamConfig]
):
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
        stream_id: str | None = None,
        event_types: list[str] | None = None,
        initializer: StreamInitializer | None = None,
    ) -> None:
        super().__init__(actor_id)
        self._hub = hub
        self._stream_id = stream_id
        self._event_types = event_types or []
        self._initializer = initializer
        self._sequence = 0
        self._received_events: list[DataEvent] = []
        self._initialized = False

    @classmethod
    def from_dict(
        cls,
        config: DataHubDataStreamConfig,
    ) -> "DataHubDataStream":
        # Get hub from registry (set by trial builder)
        hub: DataHub | None = None
        
        hub_registry: dict[str, DataHub] = getattr(cls, "_hub_registry", {})
        
        if hub_registry:
            hub_id = config.get("hub_id", "default_hub")
            hub = hub_registry.get(hub_id)

        if hub is None:
            # Fallback: create new hub (shouldn't happen in normal flow)
            hub_id = config.get("hub_id", "default_hub")
            persistence_file = config.get("persistence_file", "outputs/events.jsonl")
            hub = DataHub(hub_id=hub_id, persistence_file=persistence_file)

        return cls(
            actor_id=config["actor_id"],
            hub=hub,
            stream_id=config.get("stream_id"),
            event_types=config.get("event_types", []),
        )

    async def start(self) -> None:
        """Protocol hook: subscribe to DataHub events and run initializer if provided."""
        if self._hub is None:
            raise RuntimeError(f"stream '{self.actor_id}' has no DataHub instance")

        LOGGER.info(
            "stream '%s' starting: stream_id=%s event_types=%s",
            self.actor_id,
            self._stream_id,
            self._event_types,
        )

        # Run initializer if provided (e.g., trigger initial searches)
        if self._initializer and not self._initialized:
            await self._initializer.initialize(self)
            self._initialized = True

        # Subscribe to DataHub events using callback mechanism
        def event_callback(event: DataEvent) -> None:
            # Check if this event matches our subscription
            should_forward = False
            if self._stream_id and event.event_type == self._stream_id:
                should_forward = True
            elif self._event_types and event.event_type in self._event_types:
                should_forward = True

            if should_forward:
                self._received_events.append(event)
                # Schedule async publish
                asyncio.create_task(self._publish_event(event))

        # Determine event types to subscribe to
        subscribe_event_types = self._event_types.copy()
        if self._stream_id and self._stream_id not in subscribe_event_types:
            subscribe_event_types.append(self._stream_id)

        # Subscribe to DataHub using subscribe_agent mechanism
        # We use the stream's actor_id as the agent_id for subscription
        # DataHub stores the callback internally, so we don't need to keep a reference
        for event_type in subscribe_event_types:
            self._hub.subscribe_agent(
                agent_id=self.actor_id,
                event_types=[event_type],
                callback=event_callback,
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
        LOGGER.info(
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
            "received_events": len(self._received_events),
            "initialized": self._initialized,
        }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Protocol hook: dashboard restores a checkpoint before resuming."""
        self._sequence = int(state.get("sequence", 0))
        self._initialized = bool(state.get("initialized", False))
        LOGGER.info(
            "stream '%s' restored: sequence=%d initialized=%s",
            self.actor_id,
            self._sequence,
            self._initialized,
        )

    @property
    def hub(self) -> DataHub:
        """Get the underlying DataHub instance."""
        if self._hub is None:
            raise RuntimeError(f"stream '{self.actor_id}' has no DataHub instance")
        return self._hub
