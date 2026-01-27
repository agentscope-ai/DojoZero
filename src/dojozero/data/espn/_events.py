"""ESPN base event types.

These are generic event types that work across all ESPN-supported sports.
Sport-specific modules can extend these or define their own specialized events.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dojozero.data._models import DataEvent, register_event


# =============================================================================
# Game Lifecycle Events (Generic)
# =============================================================================


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class ESPNGameInitializeEvent(DataEvent):
    """Generic game initialization event.

    Emitted when a game is first detected from the scoreboard.
    Contains basic information about the matchup.
    """

    game_id: str = field(default="")  # ESPN event ID for the game
    sport: str = field(default="")  # e.g., "football", "basketball"
    league: str = field(default="")  # e.g., "nfl", "nba", "eng.1"
    home_team: str = field(default="")  # Full team name
    away_team: str = field(default="")  # Full team name
    home_team_id: str = field(default="")
    away_team_id: str = field(default="")
    home_team_abbreviation: str = field(default="")
    away_team_abbreviation: str = field(default="")
    venue: str = field(default="")
    game_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Sport-specific metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def event_type(self) -> str:
        return "espn_game_initialize"


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class ESPNGameStartEvent(DataEvent):
    """Generic game start event.

    Emitted when a game transitions from scheduled to in-progress.
    """

    game_id: str = field(default="")  # ESPN event ID for the game
    sport: str = field(default="")
    league: str = field(default="")

    @property
    def event_type(self) -> str:
        return "espn_game_start"


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class ESPNGameEndEvent(DataEvent):
    """Generic game end event.

    Emitted when a game is marked as final.
    """

    game_id: str = field(default="")  # ESPN event ID for the game
    sport: str = field(default="")
    league: str = field(default="")
    winner: str = field(default="")  # "home", "away", or "" for tie/draw
    home_score: int = field(default=0)
    away_score: int = field(default=0)
    home_team: str = field(default="")
    away_team: str = field(default="")

    @property
    def event_type(self) -> str:
        return "espn_game_end"


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class ESPNGameUpdateEvent(DataEvent):
    """Generic game update event with current state.

    Contains the current score and game state.
    Sport-specific details are in the metadata dict.
    """

    game_id: str = field(default="")  # ESPN event ID for the game
    sport: str = field(default="")
    league: str = field(default="")
    home_score: int = field(default=0)
    away_score: int = field(default=0)
    period: int = field(default=0)  # Quarter, half, inning, set, etc.
    clock: str = field(default="")  # Game clock display
    status: str = field(default="")  # Status description
    # Sport-specific data (team stats, player stats, etc.)
    home_team_data: dict[str, Any] = field(default_factory=dict)
    away_team_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def event_type(self) -> str:
        return "espn_game_update"


# =============================================================================
# Play Events (Generic)
# =============================================================================


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class ESPNPlayEvent(DataEvent):
    """Generic play-by-play event.

    Represents a single play/action in a game.
    Sport-specific details are in the metadata dict.
    """

    game_id: str = field(default="")  # ESPN event ID for the game
    play_id: str = field(default="")  # Unique play ID
    sport: str = field(default="")
    league: str = field(default="")
    sequence_number: int = field(default=0)
    period: int = field(default=0)
    clock: str = field(default="")
    play_type: str = field(default="")  # Type of play
    description: str = field(default="")  # Full description
    home_score: int = field(default=0)
    away_score: int = field(default=0)
    is_scoring_play: bool = field(default=False)
    score_value: int = field(default=0)
    team_id: str = field(default="")
    team_abbreviation: str = field(default="")
    # Sport-specific data
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def event_type(self) -> str:
        return "espn_play"


# =============================================================================
# Odds Events
# =============================================================================


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class ESPNOddsUpdateEvent(DataEvent):
    """Generic odds update event.

    Contains betting odds from ESPN's data (typically from DraftKings, FanDuel, etc.)
    """

    game_id: str = field(default="")  # ESPN event ID for the game
    sport: str = field(default="")
    league: str = field(default="")
    provider: str = field(default="")  # e.g., "Draft Kings", "FanDuel"
    # Spread betting
    spread: float = field(default=0.0)  # Positive = home favored
    spread_odds_home: int = field(default=-110)
    spread_odds_away: int = field(default=-110)
    # Over/Under (total)
    over_under: float = field(default=0.0)
    over_odds: int = field(default=-110)
    under_odds: int = field(default=-110)
    # Moneyline
    moneyline_home: int = field(default=0)
    moneyline_away: int = field(default=0)
    # Team info for context
    home_team: str = field(default="")
    away_team: str = field(default="")

    @property
    def event_type(self) -> str:
        return "espn_odds_update"


# =============================================================================
# Utility Types
# =============================================================================


@dataclass(slots=True, frozen=True)
class ESPNTeamInfo:
    """Basic team information from ESPN."""

    team_id: str = field(default="")
    name: str = field(default="")  # Full display name
    abbreviation: str = field(default="")
    location: str = field(default="")  # City/location
    color: str = field(default="")  # Primary color hex
    logo_url: str = field(default="")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ESPNTeamInfo":
        """Create from ESPN API team dict."""
        return cls(
            team_id=str(data.get("id", "")),
            name=data.get("displayName", ""),
            abbreviation=data.get("abbreviation", ""),
            location=data.get("location", ""),
            color=data.get("color", ""),
            logo_url=data.get("logo", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict."""
        return {
            "teamId": self.team_id,
            "name": self.name,
            "abbreviation": self.abbreviation,
            "location": self.location,
            "color": self.color,
            "logoUrl": self.logo_url,
        }


@dataclass(slots=True, frozen=True)
class ESPNCompetitor:
    """Competitor (team) in a game with score."""

    team: ESPNTeamInfo = field(default_factory=ESPNTeamInfo)
    home_away: str = field(default="")  # "home" or "away"
    score: int = field(default=0)
    winner: bool = field(default=False)
    records: list[dict[str, Any]] = field(default_factory=list)
    line_scores: list[int] = field(default_factory=list)  # Period scores

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ESPNCompetitor":
        """Create from ESPN API competitor dict."""
        team_data = data.get("team", {})
        line_scores = [
            int(ls.get("value", 0) or 0)
            for ls in data.get("linescores", [])
            if isinstance(ls, dict)
        ]
        return cls(
            team=ESPNTeamInfo.from_dict(team_data),
            home_away=data.get("homeAway", ""),
            score=int(data.get("score", 0) or 0),
            winner=bool(data.get("winner", False)),
            records=data.get("records", []),
            line_scores=line_scores,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict."""
        return {
            "team": self.team.to_dict(),
            "homeAway": self.home_away,
            "score": self.score,
            "winner": self.winner,
            "records": self.records,
            "lineScores": self.line_scores,
        }
