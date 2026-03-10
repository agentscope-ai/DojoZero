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
    │           └── PreGameStatsEvent (unified pregame stats)
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
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


# =============================================================================
# Value Objects (shared across events, metadata, stores, API responses)
# =============================================================================


class PlayerIdentity(BaseModel):
    """Player identification for roster/lineup data.

    Included in :class:`TeamIdentity` so that ``GameInitializeEvent``
    carries per-team rosters and headshot URLs.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    player_id: str = Field(default="", serialization_alias="playerId")
    name: str = ""
    position: str = ""  # e.g., "G", "F", "C", "QB", "WR", "LB"
    jersey: str = ""
    headshot_url: str = Field(default="", serialization_alias="headshotUrl")
    group: str = ""  # e.g., "offense", "defense", "specialTeam" (NFL only)


class TeamIdentity(BaseModel):
    """Single representation for a team across the entire system.

    Used in events, trial metadata, arena server responses, and frontend data.
    Captures all team data from ESPN API at discovery time so downstream
    components never need to re-fetch or hardcode team info.

    Serialization aliases produce camelCase keys matching the frontend contract
    when serialized with ``model_dump(by_alias=True)``.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    team_id: str = Field(default="", serialization_alias="teamId")
    name: str = ""  # Full display name, e.g., "Boston Celtics"
    tricode: str = Field(default="", serialization_alias="abbrev")  # e.g., "BOS"
    location: str = Field(default="", serialization_alias="city")  # e.g., "Boston"
    color: str = ""  # Primary hex color
    alternate_color: str = Field(default="", serialization_alias="alternateColor")
    logo_url: str = Field(default="", serialization_alias="logoUrl")
    record: str = ""  # Win-loss record, e.g., "42-18"
    players: list[PlayerIdentity] = Field(default_factory=list)

    def __str__(self) -> str:
        return self.name

    def __bool__(self) -> bool:
        return bool(self.name)


# US state abbreviation to IANA timezone mapping for NBA/NFL venues
US_STATE_TO_TIMEZONE: dict[str, str] = {
    # Eastern Time (UTC-5 / UTC-4 DST)
    "NY": "America/New_York",
    "MA": "America/New_York",
    "FL": "America/New_York",
    "GA": "America/New_York",
    "NC": "America/New_York",
    "SC": "America/New_York",
    "OH": "America/New_York",
    "IN": "America/Indiana/Indianapolis",
    "MI": "America/Detroit",
    "PA": "America/New_York",
    "DC": "America/New_York",
    "MD": "America/New_York",
    "VA": "America/New_York",
    "NJ": "America/New_York",
    "CT": "America/New_York",
    "RI": "America/New_York",
    "NH": "America/New_York",
    "VT": "America/New_York",
    "ME": "America/New_York",
    "DE": "America/New_York",
    "WV": "America/New_York",
    "KY": "America/New_York",
    # Central Time (UTC-6 / UTC-5 DST)
    "IL": "America/Chicago",
    "TX": "America/Chicago",
    "MN": "America/Chicago",
    "WI": "America/Chicago",
    "TN": "America/Chicago",
    "OK": "America/Chicago",
    "LA": "America/Chicago",
    "MO": "America/Chicago",
    "AR": "America/Chicago",
    "MS": "America/Chicago",
    "AL": "America/Chicago",
    "IA": "America/Chicago",
    "KS": "America/Chicago",
    "NE": "America/Chicago",
    "SD": "America/Chicago",
    "ND": "America/Chicago",
    # Mountain Time (UTC-7 / UTC-6 DST)
    "CO": "America/Denver",
    "UT": "America/Denver",
    "AZ": "America/Phoenix",  # No DST
    "NM": "America/Denver",
    "MT": "America/Denver",
    "WY": "America/Denver",
    "ID": "America/Boise",
    # Pacific Time (UTC-8 / UTC-7 DST)
    "CA": "America/Los_Angeles",
    "OR": "America/Los_Angeles",
    "WA": "America/Los_Angeles",
    "NV": "America/Los_Angeles",
    # Alaska/Hawaii (rare for major sports)
    "AK": "America/Anchorage",
    "HI": "Pacific/Honolulu",
}

# Default timezone when state is unknown
DEFAULT_TIMEZONE = "America/New_York"


def get_timezone_for_state(state: str) -> str:
    """Get IANA timezone for a US state abbreviation.

    Args:
        state: Two-letter US state abbreviation (e.g., "NY", "CA").

    Returns:
        IANA timezone string (e.g., "America/New_York").
        Defaults to America/New_York if state is unknown.
    """
    return (
        US_STATE_TO_TIMEZONE.get(state.upper(), DEFAULT_TIMEZONE)
        if state
        else DEFAULT_TIMEZONE
    )


class VenueInfo(BaseModel):
    """Venue/stadium information."""

    model_config = ConfigDict(frozen=True)

    venue_id: str = ""
    name: str = ""
    city: str = ""
    state: str = ""
    indoor: bool = True
    timezone: str = ""  # IANA timezone (e.g., "America/New_York")


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
    home_odds: float = 1.0  # Decimal odds (1 / home_probability)
    away_odds: float = 1.0  # Decimal odds (1 / away_probability)


class TotalOdds(BaseModel):
    """Over/under (totals) market odds.

    The total line is the combined score threshold.
    Probabilities and odds describe the over/under outcomes.
    """

    model_config = ConfigDict(frozen=True)

    total: float = 0.0  # e.g., 220.5
    over_probability: float = 0.0
    under_probability: float = 0.0
    over_odds: float = 1.0  # Decimal odds (1 / over_probability)
    under_odds: float = 1.0  # Decimal odds (1 / under_probability)


class OddsInfo(BaseModel):
    """All odds markets for a game from a single provider.

    Each market is optional since providers may not offer all market types,
    and updates may arrive for individual markets independently.
    Spreads and totals are lists since providers may offer multiple lines.
    """

    model_config = ConfigDict(frozen=True)

    provider: str = ""  # e.g., "polymarket"
    moneyline: MoneylineOdds | None = None
    spreads: list[SpreadOdds] = Field(default_factory=list)
    totals: list[TotalOdds] = Field(default_factory=list)


# =============================================================================
# Pre-Game Stats Value Objects
# =============================================================================


class SeasonSeries(BaseModel):
    """Head-to-head record between two teams this season."""

    model_config = ConfigDict(frozen=True)

    total_games: int = 0
    home_wins: int = 0
    away_wins: int = 0
    games: list[dict[str, Any]] = Field(default_factory=list)


class TeamRecentForm(BaseModel):
    """Recent form (last N games) for a team."""

    model_config = ConfigDict(frozen=True)

    team_id: str = ""
    team_name: str = ""
    last_n: int = 10
    wins: int = 0
    losses: int = 0
    streak: str = ""  # e.g., "W3", "L2"
    games: list[dict[str, Any]] = Field(default_factory=list)
    avg_points_scored: float = 0.0
    avg_points_allowed: float = 0.0


class ScheduleDensity(BaseModel):
    """Schedule density / rest info for a team."""

    model_config = ConfigDict(frozen=True)

    team_id: str = ""
    team_name: str = ""
    days_rest: int = 0
    is_back_to_back: bool = False
    games_last_7_days: int = 0
    games_last_14_days: int = 0


class TeamSeasonStats(BaseModel):
    """Team season statistical averages."""

    model_config = ConfigDict(frozen=True)

    team_id: str = ""
    team_name: str = ""
    stats: dict[str, float] = Field(default_factory=dict)
    rank: dict[str, int] = Field(default_factory=dict)


class HomeAwaySplits(BaseModel):
    """Home vs away performance splits."""

    model_config = ConfigDict(frozen=True)

    team_id: str = ""
    team_name: str = ""
    home_record: str = ""
    away_record: str = ""
    home_stats: dict[str, float] = Field(default_factory=dict)
    away_stats: dict[str, float] = Field(default_factory=dict)


class TeamPlayerStats(BaseModel):
    """Key player stats for a team."""

    model_config = ConfigDict(frozen=True)

    team_id: str = ""
    team_name: str = ""
    players: list[dict[str, Any]] = Field(default_factory=list)


class TeamStandings(BaseModel):
    """Conference and division standings for a team."""

    model_config = ConfigDict(frozen=True)

    team_id: str = ""
    team_name: str = ""
    conference: str = ""
    conference_rank: int = 0
    division: str = ""
    division_rank: int = 0
    overall_record: str = ""
    conference_record: str = ""
    games_back: float = 0.0


# Type variable for event classes
EventT = TypeVar("EventT", bound="DataEvent")


class PollProfile(str, Enum):
    """Polling interval profile based on game phase.

    Used by stores to dynamically adjust polling frequency
    as the game progresses through different phases.
    """

    PRE_GAME = "pre_game"
    IN_GAME = "in_game"
    LATE_GAME = "late_game"
    POST_GAME = "post_game"


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
    PREGAME_STATS = "event.pregame_stats"


def register_event(event_class: type[EventT]) -> type[EventT]:
    """No-op decorator, retained for source compatibility.

    Event dispatch now uses Pydantic discriminated unions via
    ``deserialize_data_event()`` in ``dojozero.data``.
    """
    return event_class


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

    Tries multiple field names (snake_case and camelCase) for compatibility
    with both Python model dumps and JSON API responses:
    - game_id / gameId
    - event_id / eventId (fallback)

    Handles event_id formats like "0022400608_pbp_188" by extracting
    the first segment (the actual game_id).

    Args:
        event_dict: Dictionary representation of an event

    Returns:
        Extracted game_id string, or empty string if not found
    """
    # Try game_id variants first, then event_id variants
    raw_id = (
        event_dict.get("game_id")
        or event_dict.get("gameId")
        or event_dict.get("event_id")
        or event_dict.get("eventId", "")
    )
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
    uid: str = Field(default_factory=lambda: uuid4().hex)

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    game_timestamp: datetime | None = None

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

    def get_dedup_key(self) -> str | None:
        """Return deduplication key for this event, or None if not deduplicated.

        Override in subclasses to define deduplication behavior:
        - Return None: Event is not deduplicated (default)
        - Return a unique key: Event is deduplicated by this key

        The key should be unique within the scope of the event type.
        Common patterns:
        - One-shot events: "{game_id}_{event_type}"
        - Continuous events: "{game_id}_{event_type}_{unique_id}"
        """
        return None


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

    home_team: TeamIdentity | str = Field(default_factory=lambda: TeamIdentity())
    away_team: TeamIdentity | str = Field(default_factory=lambda: TeamIdentity())
    venue: VenueInfo = Field(default_factory=VenueInfo)
    game_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broadcast: str = ""
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

    def get_dedup_key(self) -> str | None:
        """Return dedup key for one-shot game initialize event."""
        if self.game_id:
            return f"{self.game_id}_{self.event_type}"
        return None


@register_event
class GameStartEvent(GameEvent):
    """Game start event signaling transition from scheduled to in-progress."""

    home_starters: list[PlayerIdentity] = Field(default_factory=list)
    away_starters: list[PlayerIdentity] = Field(default_factory=list)

    event_type: Literal["event.game_start"] = "event.game_start"

    def get_dedup_key(self) -> str | None:
        """Return dedup key for one-shot game start event."""
        if self.game_id:
            return f"{self.game_id}_{self.event_type}"
        return None


@register_event
class GameResultEvent(GameEvent):
    """Game result event with winner and final scores."""

    winner: str = ""  # "home", "away", or "" for tie
    home_score: int = 0
    away_score: int = 0
    home_team_name: str = ""
    away_team_name: str = ""
    home_team_id: str = ""
    away_team_id: str = ""

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

    def get_dedup_key(self) -> str | None:
        """Return dedup key for one-shot game result event."""
        if self.game_id:
            return f"{self.game_id}_{self.event_type}"
        return None


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

    Pregame events are one-shot (fetched once per game), so dedup key is
    "{game_id}_{event_type}" to prevent re-fetching on resume.
    """

    source: str = ""  # e.g., "websearch", "espn_stats", "twitter", "news"

    event_type: Literal["event.pre_game_insight"] = "event.pre_game_insight"

    def get_dedup_key(self) -> str | None:
        """Return dedup key for one-shot pregame events."""
        if self.game_id:
            return f"{self.game_id}_{self.event_type}"
        return None


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
