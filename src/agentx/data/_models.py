"""Core data models: DataEvent and DataFact."""

from abc import ABC
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

# Event registry for reconstruction during replay
_EVENT_REGISTRY: dict[str, type["DataEvent"]] = {}


def register_event(event_class: type["DataEvent"] | None = None):
    """Decorator to register an event class in the registry.
    
    Usage:
        @register_event
        @dataclass(slots=True, frozen=True)
        class MyEvent(DataEvent):
            ...
    
    Or with explicit event_type:
        @register_event
        @dataclass(slots=True, frozen=True)
        class MyEvent(DataEvent):
            @property
            def event_type(self) -> str:
                return "custom_type"
    
    Args:
        event_class: Event class to register (when used as decorator)
        
    Returns:
        The decorated class
    """
    def decorator(cls: type["DataEvent"]) -> type["DataEvent"]:
        # Create a minimal instance to get the actual event_type
        # All event classes have default values for their fields
        try:
            instance = cls()
            event_type = instance.event_type
            _EVENT_REGISTRY[event_type] = cls
        except Exception:
            # Fallback: use class name if instantiation fails
            class_name = cls.__name__
            event_type = class_name.lower().replace("event", "")
            _EVENT_REGISTRY[event_type] = cls
        return cls
    
    # Support both @register_event and @register_event()
    if event_class is None:
        return decorator
    else:
        return decorator(event_class)


def get_event_class(event_type: str) -> type["DataEvent"] | None:
    """Get event class by event type.
    
    Args:
        event_type: Event type string
        
    Returns:
        Event class or None if not found
    """
    return _EVENT_REGISTRY.get(event_type)


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
        # Use dataclasses.asdict() to handle slots=True dataclasses
        event_dict = asdict(self)
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            **{k: v for k, v in event_dict.items() if k != "timestamp"},
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
        # Use dataclasses.asdict() to handle slots=True dataclasses
        fact_dict = asdict(self)
        return {
            "fact_type": self.fact_type,
            "timestamp": self.timestamp.isoformat(),
            **{k: v for k, v in fact_dict.items() if k != "timestamp"},
        }
