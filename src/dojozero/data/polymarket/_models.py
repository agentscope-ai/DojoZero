"""Models for Polymarket API data structures.

MarketOddsData is a frozen dataclass used as an internal DTO between the
Polymarket API client and store.  MarketData / EventData remain Pydantic
BaseModels because they rely on ``extra="allow"`` for flexible API parsing.
"""

from dataclasses import dataclass, field

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

    market_id: str
    home_odds: float
    away_odds: float
    slug: str | None = None
    market_type: str | None = None
    line: float | None = None
    home_probability: float = 0.0
    away_probability: float = 0.0
    token_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.home_odds <= 0.0:
            raise ValueError(f"home_odds must be > 0, got {self.home_odds}")
        if self.away_odds <= 0.0:
            raise ValueError(f"away_odds must be > 0, got {self.away_odds}")
        if not 0.0 <= self.home_probability <= 1.0:
            raise ValueError(
                f"home_probability must be in [0, 1], got {self.home_probability}"
            )
        if not 0.0 <= self.away_probability <= 1.0:
            raise ValueError(
                f"away_probability must be in [0, 1], got {self.away_probability}"
            )

    @property
    def over_odds(self) -> float | None:
        """Get over odds for totals markets, None for other market types."""
        if self.market_type == "totals":
            return self.home_odds
        return None

    @property
    def under_odds(self) -> float | None:
        """Get under odds for totals markets, None for other market types."""
        if self.market_type == "totals":
            return self.away_odds
        return None


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
    clobTokenIds: str | list[str] | None = Field(
        None, description="CLOB token IDs (may be JSON string or list)"
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
