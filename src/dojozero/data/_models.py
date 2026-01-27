"""Core data models: DataEvent and DataFact."""

from abc import ABC
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, TypeVar, get_origin, get_args, overload

# Type variable for event classes
EventT = TypeVar("EventT", bound="DataEvent")

# Event registry for reconstruction during replay
_EVENT_REGISTRY: dict[str, type["DataEvent"]] = {}


class EventTypes(str, Enum):
    """Centralized event type identifiers.

    These constants should be used wherever event_type strings are compared
    (e.g., in operators, processors, or stores) to avoid magic strings.

    Organized by domain for maintainability:
    - Polymarket/betting: odds updates
    - NBA: game lifecycle events
    - Web search: raw search results and processed summaries
    """

    # =========================================================================
    # Polymarket / Betting
    # =========================================================================
    ODDS_UPDATE = "odds_update"

    # =========================================================================
    # NBA Game Lifecycle
    # =========================================================================
    GAME_INITIALIZE = (
        "game_initialize"  # Game initialization event with team info (no odds)
    )
    GAME_START = "game_start"
    GAME_RESULT = "game_result"
    GAME_UPDATE = "game_update"
    PLAY_BY_PLAY = "play_by_play"

    # =========================================================================
    # Web Search
    # =========================================================================
    # Raw search results from API
    RAW_WEB_SEARCH = "raw_web_search"

    # Processed summaries (generated from raw_web_search)
    INJURY_SUMMARY = "injury_summary"
    POWER_RANKING = "power_ranking"
    EXPERT_PREDICTION = "expert_prediction"

    # =========================================================================
    # NFL Game Lifecycle
    # =========================================================================
    NFL_GAME_INITIALIZE = "nfl_game_initialize"
    NFL_GAME_START = "nfl_game_start"
    NFL_GAME_RESULT = "nfl_game_result"
    NFL_GAME_UPDATE = "nfl_game_update"
    NFL_PLAY = "nfl_play"
    NFL_DRIVE = "nfl_drive"
    NFL_ODDS_UPDATE = "nfl_odds_update"


@overload
def register_event(event_class: type[EventT]) -> type[EventT]: ...


@overload
def register_event(
    event_class: None = None,
) -> Callable[[type[EventT]], type[EventT]]: ...


def register_event(
    event_class: type[EventT] | None = None,
) -> type[EventT] | Callable[[type[EventT]], type[EventT]]:
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

    def decorator(cls: type[EventT]) -> type[EventT]:
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


def convert_datetime_to_iso(obj: Any) -> Any:
    """Recursively convert datetime objects to ISO format strings.

    Args:
        obj: Object that may contain datetime objects

    Returns:
        Object with all datetime objects converted to ISO format strings
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: convert_datetime_to_iso(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_datetime_to_iso(item) for item in obj]
    else:
        return obj


def extract_game_id(event_dict: dict[str, Any]) -> str:
    """Extract game_id from an event dictionary.

    Tries 'game_id' field first, then falls back to 'event_id'.
    Handles event_id formats like "0022400608_pbp_188" by extracting
    the first segment (the actual game_id).

    Args:
        event_dict: Dictionary representation of an event

    Returns:
        Extracted game_id string, or empty string if not found
    """
    raw_id = event_dict.get("game_id") or event_dict.get("event_id", "")
    if not raw_id:
        return ""

    raw_id_str = str(raw_id)
    # Handle event_id format like "0022400608_pbp_188" -> extract game_id
    if "_" in raw_id_str and raw_id_str.startswith("00"):
        return raw_id_str.split("_")[0]
    return raw_id_str


class DataEventFactory:
    """Factory for creating DataEvent instances from dictionaries.

    Handles automatic dispatch to the correct event subclass based on event_type.
    """

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DataEvent | None":
        """Create event from dictionary by automatically dispatching to the correct subclass.

        Uses the event registry to look up the correct event class based on 'event_type'.
        This is useful when you don't know the specific event class ahead of time.

        Args:
            data: Dictionary containing event data (must include 'event_type')

        Returns:
            Instance of the correct event subclass, or None if event_type not found

        Example:
            >>> data = {"event_type": "raw_web_search", "query": "test", ...}
            >>> event = DataEventFactory.from_dict(data)
            >>> assert isinstance(event, RawWebSearchEvent)
        """
        event_type = data.get("event_type")
        if not event_type:
            return None

        event_class = get_event_class(event_type)
        if not event_class:
            return None

        # Use the class's from_dict method
        return event_class.from_dict(data)


@dataclass(slots=True, frozen=True, kw_only=True)
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

        # Convert all datetime fields to ISO format strings
        converted_dict = convert_datetime_to_iso(event_dict)
        return {
            "event_type": self.event_type,
            **converted_dict,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataEvent":
        """Create event from dictionary.

        When called on a specific subclass (e.g., RawWebSearchEvent.from_dict(data)),
        creates an instance of that subclass.

        Args:
            data: Dictionary containing event data (may include 'event_type' and extra fields)

        Returns:
            Instance of the class this method is called on

        Note:
            Only fields defined on the dataclass are used. Extra fields in the dictionary
            are ignored to support forward compatibility (e.g., events from newer versions).
        """
        # Get field names and types defined on this dataclass
        field_info = {f.name: f.type for f in fields(cls)}
        field_names = set(field_info.keys())

        # Filter data to only include fields defined on the dataclass
        # This prevents TypeError when dictionary contains extra fields
        event_data = {k: v for k, v in data.items() if k in field_names}

        # Parse datetime fields if they are strings
        for field_name, field_type in field_info.items():
            if field_name in event_data and isinstance(event_data[field_name], str):
                # Check if the field type is datetime (handle both datetime and Optional[datetime])
                origin = get_origin(field_type)
                args = get_args(field_type) if origin else ()
                is_datetime_field = field_type == datetime or (
                    origin is not None and datetime in args
                )
                if is_datetime_field:
                    try:
                        event_data[field_name] = datetime.fromisoformat(
                            event_data[field_name]
                        )
                    except (ValueError, AttributeError):
                        pass  # If parsing fails, keep the original value

        return cls(**event_data)


@dataclass(slots=True, frozen=True, kw_only=True)
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

        # Convert all datetime fields to ISO format strings
        converted_dict = convert_datetime_to_iso(fact_dict)
        return {
            "fact_type": self.fact_type,
            **converted_dict,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataFact":
        """Create fact from dictionary.

        When called on a specific subclass, creates an instance of that subclass.

        Args:
            data: Dictionary containing fact data (may include 'fact_type' and extra fields)

        Returns:
            Instance of the class this method is called on

        Note:
            Only fields defined on the dataclass are used. Extra fields in the dictionary
            are ignored to support forward compatibility (e.g., facts from newer versions).
        """
        # Get field names and types defined on this dataclass
        field_info = {f.name: f.type for f in fields(cls)}
        field_names = set(field_info.keys())

        # Filter data to only include fields defined on the dataclass
        # This prevents TypeError when dictionary contains extra fields
        fact_data = {k: v for k, v in data.items() if k in field_names}

        # Parse datetime fields if they are strings
        for field_name, field_type in field_info.items():
            if field_name in fact_data and isinstance(fact_data[field_name], str):
                # Check if the field type is datetime (handle both datetime and Optional[datetime])
                origin = get_origin(field_type)
                args = get_args(field_type) if origin else ()
                is_datetime_field = field_type == datetime or (
                    origin is not None and datetime in args
                )
                if is_datetime_field:
                    try:
                        fact_data[field_name] = datetime.fromisoformat(
                            fact_data[field_name]
                        )
                    except (ValueError, AttributeError):
                        pass  # If parsing fails, keep the original value

        return cls(**fact_data)
