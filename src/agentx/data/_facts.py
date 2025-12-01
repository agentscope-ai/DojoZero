"""DataFact types: Pull-based state snapshots.

DataFacts represent current state at a point in time, optimized for fast queries.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agentx.core._types import JSONDict


@dataclass(slots=True, frozen=True)
class DataFact:
    """Base class for pull-based state snapshots.
    
    DataFacts represent current state at a point in time, optimized for:
    - Fast, one-time queries
    - Caching and indexing
    - Synchronous/blocking access patterns
    
    Key differences from DataEvent:
    - No "before" state (just current state)
    - Optimized for queries, not streaming
    - Typically cached/materialized from events
    """

    fact_type: str
    """Type identifier for the fact (e.g., 'game_score', 'current_odds')."""
    
    timestamp: datetime
    """When this fact was captured/current as of."""
    
    game_id: str | None = None
    """Optional game identifier for game-related facts."""
    
    metadata: JSONDict = field(default_factory=dict)
    """Additional context/metadata."""
    
    def to_dict(self) -> dict[str, Any]:
        """Convert fact to dictionary for serialization."""
        return {
            "fact_type": self.fact_type,
            "timestamp": self.timestamp.isoformat(),
            "game_id": self.game_id,
            "metadata": self.metadata,
            **{k: v for k, v in self.__dict__.items() if k not in {"fact_type", "timestamp", "game_id", "metadata"}},
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataFact":
        """Create fact from dictionary."""
        fact_type = data.pop("fact_type")
        timestamp_str = data.pop("timestamp")
        timestamp = datetime.fromisoformat(timestamp_str) if isinstance(timestamp_str, str) else timestamp_str
        
        # Find the appropriate subclass
        fact_classes = {
            "game_score": GameScoreFact,
            "current_odds": OddsFact,
            "game_status": GameStatusFact,
            "team_stats": TeamStatsFact,
            "player_status": PlayerStatusFact,
            "search_result": SearchResultFact,
        }
        
        fact_class = fact_classes.get(fact_type, DataFact)
        return fact_class(timestamp=timestamp, **data)


@dataclass(slots=True, frozen=True)
class GameScoreFact(DataFact):
    """Current game score snapshot."""
    
    fact_type: str = field(default="game_score", init=False)
    
    game_id: str
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int
    period: int  # Current period/quarter
    period_time: str  # Time remaining in period (e.g., "10:23")
    game_status: str  # e.g., "live", "halftime", "finished"
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class OddsFact(DataFact):
    """Current betting odds snapshot."""
    
    fact_type: str = field(default="current_odds", init=False)
    
    market_id: str
    market_question: str
    game_id: str | None = None
    outcome: str  # e.g., "Yes", "No"
    current_odds: float  # Current odds (e.g., 1.85)
    volume_24h: float  # 24-hour trading volume
    liquidity: float  # Current market liquidity
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class GameStatusFact(DataFact):
    """Current game status snapshot."""
    
    fact_type: str = field(default="game_status", init=False)
    
    game_id: str
    status: str  # e.g., "scheduled", "live", "halftime", "finished"
    scheduled_start: datetime
    actual_start: datetime | None = None
    ended_at: datetime | None = None
    home_score: int | None = None
    away_score: int | None = None
    venue: str
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class TeamStatsFact(DataFact):
    """Current team statistics snapshot."""
    
    fact_type: str = field(default="team_stats", init=False)
    
    team_id: str
    game_id: str | None = None
    period: int | None = None  # If stats are period-specific
    
    # Current stats
    points: int
    rebounds: int
    assists: int
    steals: int
    blocks: int
    turnovers: int
    fouls: int
    
    # Shooting percentages
    fg_percentage: float
    three_pt_percentage: float
    ft_percentage: float
    
    # Advanced metrics
    pace: float | None = None
    offensive_rating: float | None = None
    defensive_rating: float | None = None
    
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class PlayerStatusFact(DataFact):
    """Current player status snapshot (including injuries)."""
    
    fact_type: str = field(default="player_status", init=False)
    
    player_id: str
    player_name: str
    team_id: str
    game_id: str | None = None
    
    # Current status
    is_active: bool
    injury_status: str | None = None  # e.g., "questionable", "doubtful", "out"
    injury_type: str | None = None
    expected_return: datetime | None = None
    
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class SearchResultFact(DataFact):
    """Processed search result snapshot (after LLM processing).
    
    This fact represents the processed/aggregated search results that can be
    consumed by operators. It contains:
    - Summarized insights from search results
    - Key findings extracted by LLM
    - Relevance scores and sentiment
    - Actionable information for betting decisions
    """
    
    fact_type: str = field(default="search_result", init=False)
    
    # Search context
    query: str  # Original search query
    search_id: str  # Unique identifier for this search
    
    # Processed results (from LLM)
    summary: str  # LLM-generated summary of search results
    key_findings: list[str]  # Key findings extracted by LLM
    relevance_score: float  # Overall relevance score (0.0-1.0)
    sentiment: str  # Overall sentiment: "positive", "negative", "neutral"
    sentiment_score: float  # Numeric sentiment score (-1.0 to 1.0)
    
    # Extracted entities
    game_id: str | None = None  # Related game
    team_ids: list[str] = field(default_factory=list)  # Related teams mentioned
    player_ids: list[str] = field(default_factory=list)  # Related players mentioned
    
    # Top results summary
    top_results_count: int  # Number of top results processed
    top_sources: list[str]  # Top sources (e.g., ["ESPN", "The Athletic"])
    
    # Actionable insights (for betting)
    betting_insights: list[str] = field(default_factory=list)  # Betting-relevant insights
    confidence: float  # Confidence in insights (0.0-1.0)
    
    # Metadata
    processed_at: datetime  # When LLM processing occurred
    timestamp: datetime  # When search was performed

