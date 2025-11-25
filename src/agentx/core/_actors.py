"""Protocol definitions for AgentX actors and supporting interfaces."""

from types import MappingProxyType
from typing import (
    Any,
    Mapping,
    Protocol,
    Sequence,
    Type,
    TypeVar,
    runtime_checkable,
)

from ._types import JSONValue, StreamEvent

ActorConfig = Mapping[str, JSONValue]
ActorState = Mapping[str, JSONValue]
ConfigT = TypeVar("ConfigT", contravariant=True)
ActorT = TypeVar("ActorT", bound="Actor[Any]")


@runtime_checkable
class Actor(Protocol[ConfigT]):
    """Common lifecycle definition shared by all long-lived actors."""

    @property
    def actor_id(self) -> str:
        """Stable identifier that stays constant across checkpoints."""
        ...

    @classmethod
    def from_dict(
        cls: Type[ActorT],
        config: ConfigT,
        *,
        context: "ActorRuntimeContext | None" = None,
    ) -> ActorT:
        """Build a configured actor from a serialized configuration payload and runtime context."""
        ...

    async def start(self) -> None:
        """Start background tasks and attach to external resources."""
        ...

    async def stop(self) -> None:
        """Flush buffers and stop background activity gracefully."""
        ...

    async def save_state(self) -> ActorState:
        """Return a serializable snapshot for checkpointing."""
        ...

    async def load_state(self, state: ActorState) -> None:
        """Restore state from a previously captured checkpoint payload."""
        ...


@runtime_checkable
class DataStream(Actor[ConfigT], Protocol[ConfigT]):
    """Actor that publishes :class:`StreamEvent` objects to interested consumers."""

    @property
    def consumers(self) -> Sequence[str]:
        """Return the actor IDs currently subscribed to this stream."""
        ...


@runtime_checkable
class Operator(Actor[ConfigT], Protocol[ConfigT]):
    """Actor that handles synchronous requests and stateful operations."""

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process asynchronous data delivered by a :class:`DataStream`."""
        ...


@runtime_checkable
class Agent(Actor[ConfigT], Protocol[ConfigT]):
    """Actor that consumes streams and acts."""

    async def handle_stream_event(self, event: StreamEvent[Any]) -> None:
        """Process asynchronous data delivered by a :class:`DataStream`."""
        ...

    @property
    def operators(self) -> Sequence[str]:
        """Return the operator IDs the agent can reach."""
        ...


class ActorRuntimeContext:
    """Dashboard-supplied registry of initialized actor instances."""

    __slots__ = ("agents", "operators", "data_streams")

    def __init__(
        self,
        *,
        agents: Mapping[str, Agent[Any]] | None = None,
        operators: Mapping[str, Operator[Any]] | None = None,
        data_streams: Mapping[str, DataStream[Any]] | None = None,
    ) -> None:
        self.agents: Mapping[str, Agent[Any]] = MappingProxyType(dict(agents or {}))
        self.operators: Mapping[str, Operator[Any]] = MappingProxyType(
            dict(operators or {})
        )
        self.data_streams: Mapping[str, DataStream[Any]] = MappingProxyType(
            dict(data_streams or {})
        )
