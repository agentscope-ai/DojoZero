"""Shared data models and lightweight type aliases for AgentX."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Generic, Mapping, MutableMapping, Sequence, TypeVar

JSONPrimitive = str | int | float | bool | None
JSONValue = JSONPrimitive | Sequence["JSONValue"] | Mapping[str, "JSONValue"]
JSONDict = MutableMapping[str, JSONValue]

PayloadT = TypeVar("PayloadT")


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp for event defaults."""

    return datetime.now(timezone.utc)


@dataclass(slots=True)
class StreamEvent(Generic[PayloadT]):
    """Envelope for data emitted by a :class:`DataStream`."""

    stream_id: str  # Actor ID of the DataStream producing the payload.
    payload: PayloadT
    emitted_at: datetime = field(default_factory=_utcnow)
    sequence: int | None = None
    metadata: JSONDict = field(default_factory=dict)
