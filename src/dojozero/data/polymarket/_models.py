"""Models for Polymarket API data structures.

MarketOddsData is a frozen dataclass used as an internal DTO between the
Polymarket API client and store.  MarketData / EventData remain Pydantic
BaseModels because they rely on ``extra="allow"`` for flexible API parsing.
"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


@dataclass(slots=True, frozen=True)
class MarketOddsData:
    """Odds data for a single market.

    Represents the odds information fetched from a Polymarket market.

    Field semantics vary by market_type:
    - moneyline: home_odds = home team, away_odds = away team
    - spreads: home_odds = home team with spread, away_odds = away team with spread
    - totals: home_odds = over odds, away_odds = under odds
    """

    market_id: str = Field(..., description="Polymarket market ID")
    slug: str | None = Field(None, description="Market slug")
    market_type: str | None = Field(
        None, description="Market type: moneyline, spreads, or totals"
    )
    line: float | None = Field(
        None, description="Spread or total line value (None for moneyline)"
    )
    home_probability: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Home team probability (moneyline/spreads) or over probability (totals). "
            "For totals: this represents the probability for the over bet. "
            "The prices probabilities displayed on Polymarket are the midpoint of the bid-ask spread in the orderbook."
        ),
    )
    away_probability: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Away team probability (moneyline/spreads) or under probability (totals). "
            "For totals: this represents the probability for the under bet. "
            "The prices probabilities displayed on Polymarket are the midpoint of the bid-ask spread in the orderbook."
        ),
    )
    home_odds: float = Field(
        1.0,
        gt=0.0,
        description=(
            "Home team odds (decimal format, computed as 1 / home_probability). "
            "For totals: represents over odds."
        ),
    )
    away_odds: float = Field(
        1.0,
        gt=0.0,
        description=(
            "Away team odds (decimal format, computed as 1 / away_probability). "
            "For totals: represents under odds."
        ),
    )


class MarketData(BaseModel):
    """Market data from Polymarket API.

    Represents a market returned by the Polymarket Gamma API.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Market ID")
    slug: str | None = Field(None, description="Market slug")
    question: str | None = Field(None, description="Market question")
    sportsMarketType: str | None = Field(
        None, description="Market type (e.g., 'moneyline', 'spreads', 'totals')"
    )
    line: float | None = Field(None, description="Spread or total line value")
    active: bool = Field(True, description="Whether the market is active")
    outcomes: str | list[str] | None = Field(
        None,
        description="Market outcomes as JSON string or list (e.g., ['Over', 'Under'] or ['Home', 'Away'])",
    )
    outcomePrices: str | list[str] | None = Field(
        None,
        description="Outcome prices as JSON string or list (e.g., ['0.495', '0.505'])",
    )


class EventData(BaseModel):
    """Event data from Polymarket API.

    Represents an event returned by the Polymarket Gamma API.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Event ID")
    slug: str = Field(..., description="Event slug")
    title: str | None = Field(None, description="Event title")
    markets: list[MarketData] = Field(
        default_factory=list, description="Markets for this event"
    )
