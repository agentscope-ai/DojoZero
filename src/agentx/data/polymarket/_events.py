"""Polymarket-specific event types."""

from dataclasses import dataclass, field
from typing import Any

from agentx.data._models import DataEvent


@dataclass(slots=True, frozen=True)
class RawOddsChangeEvent(DataEvent):
    """Raw odds change event from Polymarket API."""
    
    market_id: str = field(default="")
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    
    @property
    def event_type(self) -> str:
        return "raw_odds_change"


@dataclass(slots=True, frozen=True)
class OddsChangeEvent(DataEvent):
    """Processed odds change event."""
    
    market_id: str = field(default="")
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    
    @property
    def event_type(self) -> str:
        return "odds_change"

