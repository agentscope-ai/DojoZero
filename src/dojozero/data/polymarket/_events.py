"""Polymarket-specific event types."""

from dataclasses import dataclass, field
from dojozero.data.polymarket._models import MarketOddsData

from dojozero.data._models import DataEvent, EventTypes, register_event


@register_event
@dataclass(slots=True, frozen=True)
class OddsUpdateEvent(DataEvent):
    """Odds update event from Polymarket during pregame or in-game.

    Note: home_odds and away_odds are computed from raw probabilities.
    Raw probabilities are also included for reference.
    """

    # ESPN game ID for this event; broker expects a `game_id` attribute
    game_id: str = field(default="")
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
    # Note: for spreads, `MarketOddsData.line` is the spread value.
    spread_updates: list[MarketOddsData] = field(default_factory=list)
    # Note: for totals, `MarketOddsData.line` is the total value; `home_odds`=over, `away_odds`=under.
    total_updates: list[MarketOddsData] = field(default_factory=list)

    @property
    def event_type(self) -> str:
        return EventTypes.ODDS_UPDATE.value
