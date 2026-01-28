"""Polymarket-specific event types."""

from dataclasses import dataclass, field
from typing import Any

from dojozero.data._models import DataEvent, EventTypes, register_event


@register_event
@dataclass(slots=True, frozen=True)
class OddsUpdateEvent(DataEvent):
    """Odds update event from Polymarket during pregame or in-game.

    Note: home_odds and away_odds are computed from raw probabilities.
    Raw probabilities are also included for reference.

    spread_updates: List of spread updates, each with {"spread": float, "home_odds": float, "away_odds": float}
    total_updates: List of total updates, each with {"total": float, "over_odds": float, "under_odds": float}
    """

    game_id: str = field(default="")  # ESPN event ID for the game
    home_tricode: str = field(default="")  # Home team code (e.g., "LAL", "KC")
    away_tricode: str = field(default="")  # Away team code (e.g., "BOS", "SF")
    home_odds: float = field(default=1.0)  # Computed: 1 / home_probability (moneyline)
    away_odds: float = field(default=1.0)  # Computed: 1 / away_probability (moneyline)
    home_probability: float = field(
        default=0.0
    )  # Raw probability from Polymarket (0-1)
    away_probability: float = field(
        default=0.0
    )  # Raw probability from Polymarket (0-1)
    spread_updates: list[dict[str, Any]] = field(
        default_factory=list
    )  # List of spread updates: [{"spread": -4.5, "home_odds": 2.02, "away_odds": 1.98}, ...]
    total_updates: list[dict[str, Any]] = field(
        default_factory=list
    )  # List of total updates: [{"total": 46.5, "over_odds": 1.87, "under_odds": 2.15}, ...]

    @property
    def event_type(self) -> str:
        return EventTypes.ODDS_UPDATE.value
