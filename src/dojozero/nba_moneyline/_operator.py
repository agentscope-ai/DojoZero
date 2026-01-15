"""Event counter operator for tracking events processed by agents."""

import asyncio
import logging
from typing import Any, Mapping, TypedDict

from dojozero.core import RuntimeContext, Operator, OperatorBase, StreamEvent

logger = logging.getLogger(__name__)


class _ActorIdConfig(TypedDict):
    actor_id: str


class EventCounterOperatorConfig(_ActorIdConfig):
    """Configuration for event counter operator."""

    pass


class EventCounterOperator(OperatorBase, Operator[EventCounterOperatorConfig]):
    """Operator that counts events processed by agents.

    Similar to CounterOperator in bounded_random, this operator maintains
    a shared count of events that agents can increment. Also tracks counts
    per event type for detailed statistics.
    """

    def __init__(self, actor_id: str, trial_id: str) -> None:
        super().__init__(actor_id, trial_id)
        self._count = 0
        self._typed_counts: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def from_dict(
        cls,
        config: EventCounterOperatorConfig,
        context: RuntimeContext,
    ) -> "EventCounterOperator":
        return cls(actor_id=str(config["actor_id"]), trial_id=context.trial_id)

    async def start(self) -> None:
        """Protocol hook: dashboard calls this before traffic is routed."""
        logger.info("operator '%s' starting", self.actor_id)

    async def stop(self) -> None:
        """Protocol hook: dashboard calls this during shutdown."""
        logger.info(
            "operator '%s' stopping at count=%d (typed_counts=%s)",
            self.actor_id,
            self._count,
            self._typed_counts,
        )

    async def handle_stream_event(
        self, event: StreamEvent[Any]
    ) -> None:  # pragma: no cover - not used
        """Protocol hook: dashboard forwards stream payloads here when routed."""
        del event

    async def save_state(self) -> Mapping[str, Any]:
        """Protocol hook: dashboard snapshot for checkpoints."""
        async with self._lock:
            return {
                "count": self._count,
                "typed_counts": dict(self._typed_counts),
            }

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Protocol hook: dashboard restores operator state on resume."""
        async with self._lock:
            self._count = int(state.get("count", 0))
            typed_counts = state.get("typed_counts", {})
            self._typed_counts = (
                {str(k): int(v) for k, v in typed_counts.items()}
                if isinstance(typed_counts, dict)
                else {}
            )
            logger.info(
                "operator '%s' restored to count=%d (typed_counts=%s)",
                self.actor_id,
                self._count,
                self._typed_counts,
            )

    async def count(self, event_type: str | None = None) -> int:
        """RPC method: increment and return the event count.

        Called by agents for each event they process.

        Args:
            event_type: Optional event type to track separately. If provided,
                increments both the total count and the typed count for this event type.

        Returns:
            The new total count after incrementing
        """
        async with self._lock:
            self._count += 1
            if event_type:
                self._typed_counts[event_type] = (
                    self._typed_counts.get(event_type, 0) + 1
                )
            logger.info(
                "operator '%s' incremented count to %d (event_type=%s, typed_counts=%s)",
                self.actor_id,
                self._count,
                event_type,
                self._typed_counts.get(event_type) if event_type else None,
            )
            return self._count

    @property
    def value(self) -> int:
        """Get the current total count value."""
        return self._count

    @property
    def typed_counts(self) -> dict[str, int]:
        """Get the current typed counts dictionary."""
        return dict(self._typed_counts)
