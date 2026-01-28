"""Pydantic models for Polymarket API data structures.

These models provide type-safe representations of Polymarket API responses
and internal data structures used throughout the Polymarket integration.
"""

from pydantic import BaseModel, ConfigDict, Field


class MarketOddsData(BaseModel):
    """Odds data for a single market.

    Represents the odds information fetched from a Polymarket market.
    """

    market_id: str = Field(..., description="Polymarket market ID")
    slug: str | None = Field(None, description="Market slug")
    market_type: str | None = Field(
        None, description="Market type: moneyline, spreads, or totals"
    )
    line: float | None = Field(
        None, description="Spread or total line value (None for moneyline)"
    )
    home_odds: float = Field(..., gt=0.0, description="Home team odds")
    away_odds: float = Field(..., gt=0.0, description="Away team odds")
    home_probability: float = Field(
        0.0, ge=0.0, le=1.0, description="Home team probability (0-1)"
    )
    away_probability: float = Field(
        0.0, ge=0.0, le=1.0, description="Away team probability (0-1)"
    )
    token_ids: list[str] = Field(
        default_factory=list, description="CLOB token IDs for this market"
    )


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
