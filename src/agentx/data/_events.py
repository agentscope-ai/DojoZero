"""DataEvent types: Push-based change events.

DataEvents represent changes/deltas in the system, optimized for streaming.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agentx.core._types import JSONDict


@dataclass(slots=True, frozen=True)
class DataEvent:
    """Base class for push-based change events.
    
    DataEvents can represent either:
    - **Incremental updates** (delta): Individual changes (e.g., "score changed by +2")
    - **Snapshot updates** (full state): Complete state refresh (e.g., "here's the current scoreboard")
    
    Optimized for:
    - Streaming/push-based access patterns
    - Change tracking (before/after state)
    - Asynchronous event-driven behavior
    
    Key differences from DataFact:
    - Represents updates (incremental or snapshot), not just current state
    - Optimized for streaming, not queries
    - Includes change metadata (before/after, deltas) for incremental updates
    - Used for reactive/event-driven agent behavior
    """

    event_type: str
    """Type identifier for the event (e.g., 'play_by_play', 'odds_change')."""
    
    timestamp: datetime
    """When the change occurred."""
    
    update_type: str = "incremental"  # "incremental" or "snapshot"
    """Type of update: 'incremental' (delta) or 'snapshot' (full state refresh).
    
    - incremental: Represents a change/delta (e.g., "score changed by +2")
    - snapshot: Represents a full state update (e.g., "here's the current scoreboard")
    """
    
    game_id: str | None = None
    """Optional game identifier for game-related events."""
    
    metadata: JSONDict = field(default_factory=dict)
    """Additional context/metadata."""
    
    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "game_id": self.game_id,
            "metadata": self.metadata,
            **{k: v for k, v in self.__dict__.items() if k not in {"event_type", "timestamp", "game_id", "metadata"}},
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataEvent":
        """Create event from dictionary."""
        event_type = data.pop("event_type")
        timestamp_str = data.pop("timestamp")
        timestamp = datetime.fromisoformat(timestamp_str) if isinstance(timestamp_str, str) else timestamp_str
        
        # Find the appropriate subclass
        event_classes = {
            "play_by_play": PlayByPlayEvent,
            "odds_change": OddsChangeEvent,
            "injury": InjuryEvent,
            "game_status": GameStatusEvent,
            "news": NewsEvent,
            "team_stats": TeamStatsEvent,
            "scoreboard_snapshot": ScoreboardSnapshotEvent,
            "google_search_result": GoogleSearchResultEvent,
        }
        
        event_class = event_classes.get(event_type, DataEvent)
        return event_class(timestamp=timestamp, **data)
    
    def get_change_magnitude(self) -> float | None:
        """Get the magnitude of change (if applicable).
        
        Returns None if not applicable for this event type.
        Override in subclasses that have quantifiable changes.
        """
        return None


@dataclass(slots=True, frozen=True)
class PlayByPlayEvent(DataEvent):
    """Individual play-by-play event from NBA games.
    
    This represents an INCREMENTAL update (delta):
    - A single play that occurred
    - Score changed by X points
    - Used for real-time play-by-play streaming
    
    For snapshot updates (full scoreboard), use ScoreboardSnapshotEvent.
    """
    
    event_type: str = field(default="play_by_play", init=False)
    update_type: str = field(default="incremental", init=False)  # Always incremental
    
    # Game context
    game_id: str
    period: int  # Quarter/period number
    period_time: str  # Time remaining in period (e.g., "10:23")
    game_time: datetime  # Absolute game time
    
    # Play details
    play_type: str  # e.g., "shot", "foul", "timeout", "substitution"
    team_id: str  # Team that made the play
    player_id: str | None = None  # Player involved (if applicable)
    
    # Scoring (change/delta)
    points: int  # Points scored (0, 1, 2, 3)
    home_score: int  # Home team score after play
    away_score: int  # Away team score after play
    
    # Play description
    description: str  # Human-readable description
    
    # Additional context
    shot_type: str | None = None  # e.g., "2PT Field Goal", "3PT Field Goal", "Free Throw"
    shot_distance: float | None = None  # Distance in feet
    is_made: bool | None = None  # Whether shot was made (for shot plays)
    
    timestamp: datetime
    
    def get_change_magnitude(self) -> float:
        """Return points scored as change magnitude."""
        return float(self.points)


@dataclass(slots=True, frozen=True)
class OddsChangeEvent(DataEvent):
    """Change in betting odds from Polymarket."""
    
    event_type: str = field(default="odds_change", init=False)
    
    # Market context
    market_id: str  # Polymarket market identifier
    market_question: str  # e.g., "Will Team A win?"
    game_id: str | None = None  # Associated NBA game (if applicable)
    
    # Odds details (change tracking)
    outcome: str  # e.g., "Yes", "No", or specific outcome
    previous_odds: float  # Previous odds (e.g., 1.85)
    current_odds: float  # Current odds (e.g., 1.92)
    odds_change: float  # Change in odds (current - previous)
    odds_change_percent: float  # Percentage change
    
    # Market metrics
    volume_24h: float | None = None  # 24-hour trading volume
    liquidity: float | None = None  # Current market liquidity
    
    timestamp: datetime
    
    def get_change_magnitude(self) -> float:
        """Return absolute percentage change as magnitude."""
        return abs(self.odds_change_percent)


@dataclass(slots=True, frozen=True)
class InjuryEvent(DataEvent):
    """Player injury update event."""
    
    event_type: str = field(default="injury", init=False)
    
    # Player context
    player_id: str
    player_name: str
    team_id: str
    game_id: str | None = None  # If injury occurs during a game
    
    # Injury details (change tracking)
    injury_type: str  # e.g., "ankle", "knee", "concussion"
    severity: str  # e.g., "questionable", "doubtful", "out"
    status: str  # e.g., "active", "inactive", "day-to-day"
    previous_status: str | None = None  # Previous status (if known)
    
    # Timing
    occurred_at: datetime  # When injury occurred
    reported_at: datetime  # When injury was reported
    expected_return: datetime | None = None  # Expected return date (if known)
    
    # Additional context
    description: str  # Detailed description
    source: str  # Source of injury report (e.g., "team", "nba", "media")
    
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class GameStatusEvent(DataEvent):
    """Game state change event (start, end, delays)."""
    
    event_type: str = field(default="game_status", init=False)
    
    game_id: str
    
    # Status details (change tracking)
    status: str  # e.g., "scheduled", "live", "halftime", "finished"
    previous_status: str | None = None  # Previous status
    
    # Game timing
    scheduled_start: datetime
    actual_start: datetime | None = None  # When game actually started
    ended_at: datetime | None = None  # When game ended
    
    # Current score (if game is live/finished)
    home_score: int | None = None
    away_score: int | None = None
    
    # Additional context
    delay_reason: str | None = None  # If postponed/delayed
    venue: str  # Game venue
    
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class NewsEvent(DataEvent):
    """News article or update event."""
    
    event_type: str = field(default="news", init=False)
    
    # Article details
    article_id: str
    title: str
    content: str | None = None  # Full content or summary
    url: str
    source: str  # News source (e.g., "ESPN", "The Athletic")
    
    # Relevance
    game_id: str | None = None  # Related game
    team_ids: list[str] = field(default_factory=list)  # Related teams
    player_ids: list[str] = field(default_factory=list)  # Related players
    
    # Sentiment (if processed)
    sentiment: str | None = None  # e.g., "positive", "negative", "neutral"
    sentiment_score: float | None = None  # Numeric sentiment score
    
    # Timing
    published_at: datetime
    discovered_at: datetime  # When our system discovered it
    
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class GoogleSearchResultEvent(DataEvent):
    """Raw Google search result event.
    
    Represents raw search results from Google Search API before processing.
    This is the initial event that gets processed by LLM-based processors.
    """
    
    event_type: str = field(default="google_search_result", init=False)
    update_type: str = field(default="snapshot", init=False)  # Search results are snapshots
    
    # Search context
    query: str  # Search query terms
    search_id: str  # Unique identifier for this search
    
    # Raw search results
    results: list[dict[str, Any]]  # Raw Google API results
    total_results: int  # Total number of results found
    search_time: float  # Time taken for search (seconds)
    
    # Context for processing
    game_id: str | None = None  # Related game (if search is game-related)
    team_ids: list[str] = field(default_factory=list)  # Related teams
    player_ids: list[str] = field(default_factory=list)  # Related players
    
    # Metadata
    search_engine: str = "google"  # Search engine used
    language: str = "en"  # Search language
    
    timestamp: datetime
    
    def get_change_magnitude(self) -> float | None:
        """Search results don't have a change magnitude."""
        return None


@dataclass(slots=True, frozen=True)
class TeamStatsEvent(DataEvent):
    """Team statistics update event."""
    
    event_type: str = field(default="team_stats", init=False)
    
    team_id: str
    game_id: str | None = None  # If stats are game-specific
    
    # Statistical categories (current values)
    points: int
    rebounds: int
    assists: int
    steals: int
    blocks: int
    turnovers: int
    fouls: int
    
    # Shooting percentages
    fg_percentage: float  # Field goal percentage
    three_pt_percentage: float  # Three-point percentage
    ft_percentage: float  # Free throw percentage
    
    # Advanced metrics
    pace: float | None = None  # Possessions per 48 minutes
    offensive_rating: float | None = None
    defensive_rating: float | None = None
    
    # Context
    period: int | None = None  # If stats are period-specific
    
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class ScoreboardSnapshotEvent(DataEvent):
    """Scoreboard snapshot update event.
    
    This represents a SNAPSHOT update (full state):
    - Complete current scoreboard state
    - Used when pulling latest scoreboard from API
    - Replaces incremental updates with full state
    
    This is different from PlayByPlayEvent which represents incremental changes.
    """
    
    event_type: str = field(default="scoreboard_snapshot", init=False)
    update_type: str = field(default="snapshot", init=False)  # Always snapshot
    
    # Game context
    game_id: str
    home_team_id: str
    away_team_id: str
    
    # Current state (full snapshot)
    home_score: int
    away_score: int
    period: int  # Current period/quarter
    period_time: str  # Time remaining in period
    game_status: str  # e.g., "live", "halftime", "finished"
    
    # Additional snapshot data
    home_team_stats: dict[str, Any] | None = None  # Full team stats if available
    away_team_stats: dict[str, Any] | None = None
    
    timestamp: datetime
    
    def get_change_magnitude(self) -> float | None:
        """Snapshot updates don't have a change magnitude."""
        return None

