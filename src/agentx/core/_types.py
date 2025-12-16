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

    stream_id: str  # Actor ID of the producer of the payload.
    payload: PayloadT
    emitted_at: datetime = field(default_factory=_utcnow)
    sequence: int | None = None
    metadata: JSONDict = field(default_factory=dict)


ResultT = TypeVar("ResultT")


@dataclass(slots=True)
class QueryResult(Generic[ResultT]):
    """Envelope for data returned from a query/on-demand request.
    
    Similar to StreamEvent but for pull-based queries rather than push-based streams.
    Provides metadata about the query (source, timing, etc.) along with the result.
    
    Example:
        # Query returns QueryResult[DataFact]
        result = await store.query_fact("game_score", game_id="game_123")
        # result.query_id - unique query identifier
        # result.result - the DataFact (or None if not found)
        # result.queried_at - when query was executed
    """

    query_id: str  # Unique identifier for this query (can be generated or provided)
    result: ResultT | None  # The query result (None if not found or error)
    queried_at: datetime = field(default_factory=_utcnow)  # When query was executed
    source_id: str | None = None  # Actor ID of the source (store/operator) that executed query
    query_params: JSONDict = field(default_factory=dict)  # Parameters used for the query
    metadata: JSONDict = field(default_factory=dict)  # Additional metadata (errors, warnings, etc.)
