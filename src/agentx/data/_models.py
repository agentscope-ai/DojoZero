"""Core data models: DataEvent and DataFact."""

from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True, frozen=True)
class DataEvent(ABC):
    """Base class for push-based incremental updates (events).
    
    Events represent raw or processed data updates that flow through the system.
    They are timestamped and typed for proper routing and processing.
    """
    
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def event_type(self) -> str:
        """Return the event type identifier."""
        return self.__class__.__name__.lower().replace("event", "")
    
    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            **{k: v for k, v in self.__dict__.items() if k != "timestamp"},
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataEvent":
        """Create event from dictionary."""
        if "timestamp" in data:
            if isinstance(data["timestamp"], str):
                data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


@dataclass(slots=True, frozen=True)
class DataFact(ABC):
    """Base class for pull-based state snapshots (facts).
    
    Facts represent processed/aggregated data that agents can query.
    They are computed from events by processors.
    """
    
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def fact_type(self) -> str:
        """Return the fact type identifier."""
        return self.__class__.__name__.lower().replace("fact", "")
    
    def to_dict(self) -> dict[str, Any]:
        """Convert fact to dictionary for serialization."""
        return {
            "fact_type": self.fact_type,
            "timestamp": self.timestamp.isoformat(),
            **{k: v for k, v in self.__dict__.items() if k != "timestamp"},
        }
