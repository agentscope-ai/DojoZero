"""Core data models: DataEvent hierarchy and shared value objects.

Event Hierarchy:
    DataEvent (base)
    ├── SportEvent (adds game_id, sport)
    │   ├── GameEvent — game state changes
    │   │   ├── [Lifecycle] GameInitializeEvent, GameStartEvent, GameResultEvent
    │   │   ├── [Atomic]   BasePlayEvent → NBAPlayEvent, NFLPlayEvent
    │   │   ├── [Segment]  BaseSegmentEvent → NFLDriveEvent
    │   │   ├── [Snapshot] BaseGameUpdateEvent → NBAGameUpdateEvent, NFLGameUpdateEvent
    │   │   └── OddsUpdateEvent
    │   └── PreGameInsightEvent — supplementary pre-game intelligence
    │       ├── WebSearchInsightEvent — insights derived from web search
    │       │   ├── InjuryReportEvent
    │       │   ├── PowerRankingEvent
    │       │   └── ExpertPredictionEvent
    │       └── StatsInsightEvent — insights derived from stats APIs
    │           ├── HeadToHeadEvent
    │           ├── TeamStatsEvent
    │           ├── PlayerStatsEvent
    │           └── RecentFormEvent
    └── (future non-sport events)

Value Objects:
    TeamIdentity - Team identification (name, tricode, colors, logo)
    VenueInfo - Venue/stadium information
    MoneylineOdds - Moneyline market odds
    SpreadOdds - Point spread market odds
    OddsInfo - Container for all odds markets from a provider
"""

from abc import ABC
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


# =============================================================================
# Value Objects (shared across events, metadata, stores, API responses)
# =============================================================================


class TeamIdentity(BaseModel):
    """Single representation for a team across the entire system.

    Used in events, trial metadata, arena server responses, and frontend data.
    Captures all team data from ESPN API at discovery time so downstream
    components never need to re-fetch or hardcode team info.
    """

    model_config = ConfigDict(frozen=True)

    team_id: str = ""
    name: str = ""  # Full display name, e.g., "Boston Celtics"
    tricode: str = ""  # Abbreviation, e.g., "BOS"
    location: str = ""  # City, e.g., "Boston"
    color: str = ""  # Primary hex color
    alternate_color: str = ""
    logo_url: str = ""
    record: str = ""  # Win-loss record, e.g., "42-18"

    def __str__(self) -> str:
        return self.name

    def __bool__(self) -> bool:
        return bool(self.name)


class VenueInfo(BaseModel):
    """Venue/stadium information."""

    model_config = ConfigDict(frozen=True)

    venue_id: str = ""
    name: str = ""
    city: str = ""
    state: str = ""
    indoor: bool = True


class MoneylineOdds(BaseModel):
    """Moneyline (match winner) market odds.

    Probabilities come directly from Polymarket (0-1 range).
    Decimal odds are computed as 1/probability.
    """

    model_config = ConfigDict(frozen=True)

    home_probability: float = 0.0
    away_probability: float = 0.0
    home_odds: float = 1.0  # Decimal odds (1 / home_probability)
    away_odds: float = 1.0  # Decimal odds (1 / away_probability)


class SpreadOdds(BaseModel):
    """Point spread market odds.

    Spread is from the home team's perspective.
    Negative spread means home team is favored (e.g., -6.5).
    """

    model_config = ConfigDict(frozen=True)

    spread: float = 0.0  # e.g., -6.5 means home favored by 6.5
    home_probability: float = 0.0  # Probability of home covering
    away_probability: float = 0.0


class OddsInfo(BaseModel):
    """All odds markets for a game from a single provider.

    Each market is optional since providers may not offer all market types,
    and updates may arrive for individual markets independently.
    """

    model_config = ConfigDict(frozen=True)

    provider: str = ""  # e.g., "polymarket"
    moneyline: MoneylineOdds | None = None
    spread: SpreadOdds | None = None
    # Future: total: TotalOdds | None = None


# Type variable for event classes
EventT = TypeVar("EventT", bound="DataEvent")


class EventTypes(str, Enum):
    """Centralized event type identifiers.

    These constants should be used wherever event_type strings are compared
    (e.g., in operators, processors, or stores) to avoid magic strings.
    """

    # =========================================================================
    # Polymarket / Betting (unified)
    # =========================================================================
    ODDS_UPDATE = "event.odds_update"

    # =========================================================================
    # Game Lifecycle (unified across sports)
    # =========================================================================
    GAME_INITIALIZE = "event.game_initialize"
    GAME_START = "event.game_start"
    GAME_RESULT = "event.game_result"

    # =========================================================================
    # NBA Game Events
    # =========================================================================
    NBA_PLAY = "event.nba_play"
    NBA_GAME_UPDATE = "event.nba_game_update"

    # =========================================================================
    # NFL Game Events
    # =========================================================================
    NFL_PLAY = "event.nfl_play"
    NFL_DRIVE = "event.nfl_drive"
    NFL_GAME_UPDATE = "event.nfl_game_update"

    # =========================================================================
    # Game Insights (web search, sentiment, etc.)
    # =========================================================================
    INJURY_REPORT = "event.injury_report"
    POWER_RANKING = "event.power_ranking"
    EXPERT_PREDICTION = "event.expert_prediction"

    # =========================================================================
    # Stats Insights (ESPN API-derived)
    # =========================================================================
    HEAD_TO_HEAD = "event.head_to_head"
    TEAM_STATS = "event.team_stats"
    PLAYER_STATS = "event.player_stats"
    RECENT_FORM = "event.recent_form"

    # =========================================================================
    # Legacy aliases (for backward compatibility with existing JSONL files)
    # =========================================================================
    PLAY_BY_PLAY = "event.play_by_play"  # Old NBA play event type
    GAME_UPDATE = "event.game_update"  # Old NBA game update type
    NFL_GAME_INITIALIZE = "event.nfl_game_initialize"  # Old NFL-specific
    NFL_GAME_START = "event.nfl_game_start"
    NFL_GAME_RESULT = "event.nfl_game_result"
    NFL_ODDS_UPDATE = "event.nfl_odds_update"


def register_event(event_class: type[EventT]) -> type[EventT]:
    """No-op decorator, retained for source compatibility.

    Event dispatch now uses Pydantic discriminated unions via
    ``deserialize_data_event()`` in ``dojozero.data``.
    """
    return event_class


def register_legacy_event_type(event_type: str, cls: type["DataEvent"]) -> None:
    """No-op, retained for source compatibility.

    Legacy event_type mapping is now handled by ``_LEGACY_EVENT_TYPE_MAP``
    in ``dojozero.data.__init__``.
    """


def convert_datetime_to_iso(obj: Any) -> Any:
    """Recursively convert datetime objects to ISO format strings.

    Args:
        obj: Object that may contain datetime objects

    Returns:
        Object with all datetime objects converted to ISO format strings
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: convert_datetime_to_iso(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_datetime_to_iso(item) for item in obj]
    else:
        return obj


def extract_game_id(event_dict: dict[str, Any]) -> str:
    """Extract game_id from an event dictionary.

    Tries 'game_id' field first, then falls back to 'event_id'.
    Handles event_id formats like "0022400608_pbp_188" by extracting
    the first segment (the actual game_id).

    Args:
        event_dict: Dictionary representation of an event

    Returns:
        Extracted game_id string, or empty string if not found
    """
    raw_id = event_dict.get("game_id") or event_dict.get("event_id", "")
    if not raw_id:
        return ""

    raw_id_str = str(raw_id)
    # Handle event_id format like "0022400608_pbp_188" -> extract game_id
    if "_" in raw_id_str and raw_id_str.startswith("00"):
        return raw_id_str.split("_")[0]
    return raw_id_str


# =============================================================================
# Base Event
# =============================================================================


class DataEvent(BaseModel, ABC):
    """Base class for push-based incremental updates (events).

    Events represent raw or processed data updates that flow through the system.
    They are timestamped and typed for proper routing and processing.

    Concrete subclasses narrow ``event_type`` to a ``Literal`` for Pydantic
    discriminated-union dispatch::

        class MyEvent(DataEvent):
            event_type: Literal["event.my_event"] = "event.my_event"
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    event_type: str = ""  # Narrowed to Literal on concrete subclasses

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for serialization."""
        event_dict = self.model_dump(mode="python")
        return convert_datetime_to_iso(event_dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataEvent":
        """Create event from dictionary.

        Only fields defined on the model are used. Extra fields in the dictionary
        are ignored to support forward compatibility.

        Args:
            data: Dictionary containing event data

        Returns:
            Instance of the class this method is called on
        """
        field_names = set(cls.model_fields.keys())
        event_data = {k: v for k, v in data.items() if k in field_names}
        return cls.model_validate(event_data)


# =============================================================================
# Sport Event (common base for game + intel events)
# =============================================================================


class SportEvent(DataEvent):
    """Base for all events related to a specific sport and game."""

    game_id: str = ""  # ESPN event ID for the game
    sport: str = ""  # "nba", "nfl"


# =============================================================================
# Game Events
# =============================================================================


class GameEvent(SportEvent):
    """Base for game state change events (lifecycle, plays, updates, odds)."""

    pass


@register_event
class GameInitializeEvent(GameEvent):
    """Game initialization event with rich team and venue data.

    Emitted when a game is first detected. Carries full team identity
    so downstream components never need to re-fetch team info.
    Replaces the old NBA GameInitializeEvent, NFL NFLGameInitializeEvent,
    and ESPN ESPNGameInitializeEvent.
    """

    home_team: TeamIdentity | str = Field(default_factory=TeamIdentity)
    away_team: TeamIdentity | str = Field(default_factory=TeamIdentity)
    venue: VenueInfo = Field(default_factory=VenueInfo)
    game_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broadcast: str = ""
    odds: OddsInfo | None = None
    season_year: int = 0
    season_type: str = ""  # "regular", "postseason", "preseason"

    @model_validator(mode="before")
    @classmethod
    def _coerce_teams(cls, values: Any) -> Any:
        """Accept plain strings for home_team/away_team, coercing to TeamIdentity."""
        if isinstance(values, dict):
            for field in ("home_team", "away_team"):
                v = values.get(field)
                if isinstance(v, str):
                    values[field] = TeamIdentity(name=v)
        return values

    event_type: Literal["event.game_initialize"] = "event.game_initialize"


@register_event
class GameStartEvent(GameEvent):
    """Game start event signaling transition from scheduled to in-progress."""

    event_type: Literal["event.game_start"] = "event.game_start"


@register_event
class GameResultEvent(GameEvent):
    """Game result event with winner and final scores."""

    winner: str = ""  # "home", "away", or "" for tie
    home_score: int = 0
    away_score: int = 0
    home_team_name: str = ""
    away_team_name: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_final_score(cls, values: Any) -> Any:
        """Accept final_score dict for backward compatibility with broker."""
        if isinstance(values, dict):
            final_score = values.pop("final_score", None)
            if isinstance(final_score, dict):
                if "home_score" not in values and "home" in final_score:
                    values["home_score"] = final_score["home"]
                if "away_score" not in values and "away" in final_score:
                    values["away_score"] = final_score["away"]
        return values

    @property
    def final_score(self) -> dict[str, int]:
        """Backward-compatible property for broker settlement."""
        return {"home": self.home_score, "away": self.away_score}

    event_type: Literal["event.game_result"] = "event.game_result"


# =============================================================================
# Tier 1: Atomic (single action as it happens)
# =============================================================================


class BasePlayEvent(GameEvent):
    """Base for atomic play-by-play events.

    Captures a single action in a game (e.g., a shot, pass, foul).
    Sport-specific subclasses add their own fields.
    """

    play_id: str = ""
    sequence_number: int = 0
    period: int = 0
    clock: str = ""
    description: str = ""
    home_score: int = 0
    away_score: int = 0
    team_id: str = ""
    team_tricode: str = ""
    is_scoring_play: bool = False
    score_value: int = 0


# =============================================================================
# Tier 2: Segment (completed unit of play)
# =============================================================================


class BaseSegmentEvent(GameEvent):
    """Base for segment events (completed unit of play).

    Captures a completed sequence of plays (e.g., an NFL drive).
    Not all sports need this tier.
    """

    segment_id: str = ""
    segment_number: int = 0
    team_id: str = ""
    team_tricode: str = ""
    start_period: int = 0
    start_clock: str = ""
    end_period: int = 0
    end_clock: str = ""
    plays_count: int = 0
    result: str = ""  # e.g., "Touchdown", "Field Goal", "Punt"
    is_score: bool = False
    points_scored: int = 0


# =============================================================================
# Tier 3: Snapshot (current game state)
# =============================================================================


class BaseGameUpdateEvent(GameEvent):
    """Base for game state snapshot events.

    Captures the full game state at a point in time (scores, stats, clock).
    Sport-specific subclasses add team stats and other details.
    """

    period: int = 0
    game_clock: str = ""
    home_score: int = 0
    away_score: int = 0
    game_time_utc: str = ""  # ISO format datetime string


# =============================================================================
# Odds
# =============================================================================


@register_event
class OddsUpdateEvent(GameEvent):
    """Odds update event from prediction market.

    Unified event for all odds sources (Polymarket, sportsbooks).
    Carries structured OddsInfo with optional market types.

    Supports two construction styles:
    - New style: OddsUpdateEvent(odds=OddsInfo(moneyline=MoneylineOdds(...)))
    - Legacy:   OddsUpdateEvent(home_odds=1.95, away_odds=2.10)
    """

    odds: OddsInfo = Field(default_factory=OddsInfo)

    # Convenience fields for backward compatibility and easy access
    home_tricode: str = ""
    away_tricode: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_odds(cls, values: Any) -> Any:
        """Accept home_odds/away_odds kwargs for backward compatibility."""
        if isinstance(values, dict):
            home_odds = values.pop("home_odds", None)
            away_odds = values.pop("away_odds", None)
            if (
                home_odds is not None or away_odds is not None
            ) and "odds" not in values:
                values["odds"] = {
                    "moneyline": {
                        "home_odds": float(home_odds) if home_odds is not None else 1.0,
                        "away_odds": float(away_odds) if away_odds is not None else 1.0,
                    }
                }
        return values

    @property
    def home_odds(self) -> float | None:
        """Backward-compatible accessor for home decimal odds."""
        if self.odds and self.odds.moneyline:
            return self.odds.moneyline.home_odds
        return None

    @property
    def away_odds(self) -> float | None:
        """Backward-compatible accessor for away decimal odds."""
        if self.odds and self.odds.moneyline:
            return self.odds.moneyline.away_odds
        return None

    event_type: Literal["event.odds_update"] = "event.odds_update"


# =============================================================================
# Pre-Game Insight Events (supplementary intelligence)
# =============================================================================


class PreGameInsightEvent(SportEvent):
    """Base for supplementary pre-game intelligence events.

    These events provide context that enriches agent decisions but are not
    game state events. Subcategories include web search insights, stats-based
    insights, sentiment analysis, news aggregation, etc.

    Concrete (non-abstract) so it can be used as a generic insight event.
    Subclasses override event_type for specific event identification.
    """

    source: str = ""  # e.g., "websearch", "espn_stats", "twitter", "news"

    event_type: Literal["event.pre_game_insight"] = "event.pre_game_insight"


class WebSearchInsightEvent(PreGameInsightEvent):
    """Base for insights derived from web search + LLM processing.

    Subclasses represent specific types of processed web search results
    (injury reports, power rankings, expert predictions). Each subclass
    handles its own search query construction and result processing.

    The raw_results field carries raw API response data through the
    processor pipeline. It defaults to empty and is typically not
    populated on the final processed events emitted to consumers.
    """

    query: str = ""
    summary: str = ""  # Human-readable summary of processed results
    raw_results: list[dict[str, Any]] = Field(default_factory=list)

    event_type: Literal["event.web_search"] = "event.web_search"


class StatsInsightEvent(PreGameInsightEvent):
    """Base for insights derived from stats APIs (e.g., ESPN).

    Carries team identification and season context so concrete subclasses
    can focus on their specific stats payload.
    """

    home_team_id: str = ""
    away_team_id: str = ""
    season_year: int = 0
    season_type: str = ""  # "regular", "postseason", "preseason"

    event_type: Literal["event.stats_insight"] = "event.stats_insight"
