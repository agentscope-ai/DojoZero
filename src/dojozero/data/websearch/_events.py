"""Web Search-specific event types."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from dojozero.data._models import DataEvent, EventTypes, register_event


class WebSearchIntent(str, Enum):
    """Web search query intent types.

    These intents specify the type of processing expected for a query,
    allowing explicit routing to specific processors.
    """

    INJURY_SUMMARY = EventTypes.INJURY_SUMMARY.value
    POWER_RANKING = EventTypes.POWER_RANKING.value
    EXPERT_PREDICTION = EventTypes.EXPERT_PREDICTION.value


@register_event
@dataclass(slots=True, frozen=True)
class RawWebSearchEvent(DataEvent):
    """Raw web search result event from API."""

    query: str = field(default="")
    results: list[dict[str, Any]] = field(default_factory=list)
    intent: WebSearchIntent | str | None = field(default=None)
    """Optional query intent.
    
    When present, this intent can be used to route events to specific processors,
    overriding the default should_process() logic.
    Can be a WebSearchIntent enum value or a string (for backward compatibility).
    """

    @property
    def event_type(self) -> str:
        return EventTypes.RAW_WEB_SEARCH.value


@register_event
@dataclass(slots=True, frozen=True)
class InjurySummaryEvent(DataEvent):
    """Injury summary event generated from web search results.

    Hybrid format: contains both human-readable summary and
    structured machine-readable data (team -> list of injured players).
    """

    query: str = field(default="")
    summary: str = field(default="")
    injured_players: dict[str, list[str]] = field(default_factory=dict)

    @property
    def event_type(self) -> str:
        return EventTypes.INJURY_SUMMARY.value


@register_event
@dataclass(slots=True, frozen=True)
class PowerRankingEvent(DataEvent):
    """Power ranking event from NBA and ESPN sources.

    Contains power rankings from multiple sources (NBA.com, ESPN, etc.)
    with structured data for easy comparison.
    """

    query: str = field(default="")
    rankings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Format: {"nba.com": [{"rank": 1, "team": "Lakers", ...}, ...], "espn.com": [...]}

    @property
    def event_type(self) -> str:
        return EventTypes.POWER_RANKING.value


@register_event
@dataclass(slots=True, frozen=True)
class ExpertPredictionEvent(DataEvent):
    """Expert prediction event from credible sources (NBA.com, ESPN, etc.).

    Contains expert predictions and analysis from authoritative sources.
    """

    query: str = field(default="")
    predictions: list[dict[str, Any]] = field(default_factory=list)
    # Format: [{"source": "nba.com", "expert": "...", "prediction": "...", ...}, ...]

    @property
    def event_type(self) -> str:
        return EventTypes.EXPERT_PREDICTION.value
