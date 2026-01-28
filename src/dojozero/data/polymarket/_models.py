"""Pydantic models for Polymarket API data structures.

These models provide type-safe representations of Polymarket API responses
and internal data structures used throughout the Polymarket integration.
"""

from pydantic import BaseModel, ConfigDict, Field


class MarketOddsData(BaseModel):
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
    home_odds: float = Field(
        ...,
        gt=0.0,
        description=(
            "Home team odds (moneyline/spreads) or over odds (totals). "
            "For totals: this represents the odds for the over bet."
        ),
    )
    away_odds: float = Field(
        ...,
        gt=0.0,
        description=(
            "Away team odds (moneyline/spreads) or under odds (totals). "
            "For totals: this represents the odds for the under bet."
        ),
    )
    home_probability: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Home team probability (moneyline/spreads) or over probability (totals). "
            "For totals: this represents the probability for the over bet."
        ),
    )
    away_probability: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Away team probability (moneyline/spreads) or under probability (totals). "
            "For totals: this represents the probability for the under bet."
        ),
    )
    token_ids: list[str] = Field(
        default_factory=list, description="CLOB token IDs for this market"
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

    model_config = ConfigDict(extra="allow")  # Allow extra fields from API response


class EventData(BaseModel):
    """Event data from Polymarket API.

    Represents an event returned by the Polymarket Gamma API.
    """

    id: str = Field(..., description="Event ID")
    slug: str = Field(..., description="Event slug")
    title: str | None = Field(None, description="Event title")
    markets: list[MarketData] = Field(
        default_factory=list, description="Markets for this event"
    )

    model_config = ConfigDict(extra="allow")  # Allow extra fields from API response
