"""Polymarket-specific event types."""

from dataclasses import dataclass, field

from dojozero.data._models import DataEvent, EventTypes, register_event


@register_event
@dataclass(slots=True, frozen=True)
class OddsUpdateEvent(DataEvent):
    """Odds update event from Polymarket during pregame or in-game.

    Note: home_odds and away_odds are computed from raw probabilities.
    Raw probabilities are also included for reference.
    """

    game_id: str = field(default="")  # ESPN event ID for the game
    home_tricode: str = field(default="")  # Home team code (e.g., "LAL", "KC")
    away_tricode: str = field(default="")  # Away team code (e.g., "BOS", "SF")
    home_odds: float = field(default=1.0)  # Computed: 1 / home_probability
    away_odds: float = field(default=1.0)  # Computed: 1 / away_probability
    home_probability: float = field(
        default=0.0
    )  # Raw probability from Polymarket (0-1)
    away_probability: float = field(
        default=0.0
    )  # Raw probability from Polymarket (0-1)

    @property
    def event_type(self) -> str:
        return EventTypes.ODDS_UPDATE.value
