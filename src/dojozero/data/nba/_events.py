"""NBA-specific event types."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dojozero.data._models import DataEvent, EventTypes, register_event


@register_event
@dataclass(slots=True, frozen=True)
class PlayByPlayEvent(DataEvent):
    """Processed NBA play-by-play event.

    Contains detailed information about a single play-by-play action,
    including event type, player info, scores, and description.
    """

    event_id: str = field(default="")  # Unique event ID: {game_id}_pbp_{action_number}
    game_id: str = field(default="")  # ESPN event ID for the game this play belongs to
    action_type: str = field(
        default=""
    )  # Action type string (e.g., "rebound", "shot", "foul", "turnover", "substitution", "timeout")
    action_number: int = field(default=0)
    period: int = field(default=0)
    clock: str = field(default="")
    person_id: int = field(default=0)  # Player ID
    player_name: str = field(default="")  # Player name (if available)
    team_tricode: str = field(default="")
    home_score: int = field(default=0)
    away_score: int = field(default=0)
    description: str = field(default="")

    @property
    def event_type(self) -> str:
        return EventTypes.PLAY_BY_PLAY.value


# =============================================================================
# Game Events
# =============================================================================


@register_event
@dataclass(slots=True, frozen=True)
class GameInitializeEvent(DataEvent):
    """Game initialization event with team information.

    This event is emitted when a game is first detected, providing
    the basic information needed to initialize a betting event.

    Contains:
    - Team information (home/away team names, game time)
    - No odds information (odds will come via OddsUpdateEvent)

    The broker can initialize the event without odds, then update
    when OddsUpdateEvent arrives.

    Note:
    - Historically this event used the ``event_id`` field to store the ESPN
      game identifier.
    - The betting broker now expects a ``game_id`` attribute on all events.
    - To keep backward compatibility, both ``game_id`` and ``event_id`` are
      present and kept in sync.
    """

    game_id: str = field(default="")  # ESPN event ID for the game
    home_team: str = field(default="")  # Full team name (e.g., "New York Knicks")
    away_team: str = field(default="")  # Full team name (e.g., "San Antonio Spurs")
    game_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def event_type(self) -> str:
        return EventTypes.GAME_INITIALIZE.value


@register_event
@dataclass(slots=True, frozen=True)
class GameStartEvent(DataEvent):
    """Game start event signaling transition from pregame to live."""

    game_id: str = field(default="")  # ESPN event ID for the game

    @property
    def event_type(self) -> str:
        return EventTypes.GAME_START.value


@register_event
@dataclass(slots=True, frozen=True)
class GameResultEvent(DataEvent):
    """Game result event with winner and final score."""

    game_id: str = field(default="")  # ESPN event ID for the game
    winner: str = field(default="")  # "home" or "away"
    final_score: dict[str, int] = field(
        default_factory=dict
    )  # {"home": 100, "away": 95}

    @property
    def event_type(self) -> str:
        return EventTypes.GAME_RESULT.value


@dataclass(slots=True, frozen=True)
class TeamStats:
    """Type-safe team statistics and metadata within a game.

    Provides structured access to team information instead of raw dict.
    Supports conversion to/from camelCase API format for backward compatibility.
    """

    team_id: int = field(default=0)
    team_name: str = field(default="")
    team_city: str = field(default="")
    team_tricode: str = field(default="")
    score: int = field(default=0)
    wins: int = field(default=0)
    losses: int = field(default=0)
    seed: int = field(default=0)
    timeouts_remaining: int = field(default=0)
    in_bonus: bool | None = field(default=None)
    periods: list[dict[str, Any]] = field(
        default_factory=list
    )  # Quarter-by-quarter scores

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamStats":
        """Create TeamStats from API dict (handles camelCase keys).

        Args:
            data: Team data dict from NBA API

        Returns:
            TeamStats instance
        """
        return cls(
            team_id=data.get("teamId", 0),
            team_name=data.get("teamName", ""),
            team_city=data.get("teamCity", ""),
            team_tricode=data.get("teamTricode", ""),
            score=data.get("score", 0),
            wins=data.get("wins", 0),
            losses=data.get("losses", 0),
            seed=data.get("seed", 0),
            timeouts_remaining=data.get("timeoutsRemaining", 0),
            in_bonus=data.get("inBonus"),
            periods=data.get("periods", []),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict (maintains original camelCase keys for API compatibility).

        Returns:
            Dict with camelCase keys matching NBA API format
        """
        return {
            "teamId": self.team_id,
            "teamName": self.team_name,
            "teamCity": self.team_city,
            "teamTricode": self.team_tricode,
            "score": self.score,
            "wins": self.wins,
            "losses": self.losses,
            "seed": self.seed,
            "timeoutsRemaining": self.timeouts_remaining,
            "inBonus": self.in_bonus,
            "periods": self.periods,
        }


@dataclass(slots=True, frozen=True)
class PlayerStats:
    """Type-safe player statistics within a game.

    Lightweight wrapper for player stats dict.
    Can be expanded with specific fields as needed.
    """

    player_id: int = field(default=0)
    name: str = field(default="")
    statistics: dict[str, Any] = field(default_factory=dict)  # Full stats dict

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlayerStats":
        """Create PlayerStats from API dict.

        Args:
            data: Player data dict from NBA API

        Returns:
            PlayerStats instance
        """
        return cls(
            player_id=data.get("personId", 0),
            name=data.get("name", ""),
            statistics=data.get("statistics", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict (API format).

        Returns:
            Dict matching NBA API format
        """
        return {
            "personId": self.player_id,
            "name": self.name,
            "statistics": self.statistics,
        }


@dataclass(slots=True, frozen=True)
class GamePlayerStats:
    """Container for home and away player stats.

    Organizes player statistics by team (home/away).
    """

    home: list[PlayerStats] = field(default_factory=list)
    away: list[PlayerStats] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GamePlayerStats":
        """Create GamePlayerStats from API dict.

        Args:
            data: Player stats dict with home/away lists

        Returns:
            GamePlayerStats instance
        """
        return cls(
            home=[PlayerStats.from_dict(p) for p in data.get("home", [])],
            away=[PlayerStats.from_dict(p) for p in data.get("away", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict (API format).

        Returns:
            Dict with home/away player lists
        """
        return {
            "home": [p.to_dict() for p in self.home],
            "away": [p.to_dict() for p in self.away],
        }


@register_event
@dataclass(slots=True, frozen=True)
class GameUpdateEvent(DataEvent):
    """Game update event with full scoreboard snapshot.

    Contains:
    - Team basic info (name, city, tricode, score, wins, losses)
    - Team stats (period scores, timeouts, bonus status)
    - All player stats (complete list of players with their statistics)

    Note: Game status (start/end) is handled by GameStartEvent and GameResultEvent from PlayByPlay.
    """

    game_id: str = field(default="")  # ESPN event ID for the game
    period: int = field(default=0)
    game_clock: str = field(default="")
    game_time_utc: str = field(default="")  # ISO format datetime string
    home_team: dict[str, Any] = field(
        default_factory=dict
    )  # Team info: teamId, teamName, teamCity, teamTricode, score, wins, losses, seed, timeoutsRemaining, inBonus, periods (quarter scores)
    away_team: dict[str, Any] = field(
        default_factory=dict
    )  # Team info: teamId, teamName, teamCity, teamTricode, score, wins, losses, seed, timeoutsRemaining, inBonus, periods (quarter scores)
    player_stats: dict[str, Any] = field(
        default_factory=dict
    )  # All player stats: {"home": [list of player dicts with statistics], "away": [list of player dicts with statistics]}

    @property
    def home_team_stats(self) -> TeamStats:
        """Type-safe access to home team stats.

        Returns:
            TeamStats instance with structured team data
        """
        return TeamStats.from_dict(self.home_team)

    @property
    def away_team_stats(self) -> TeamStats:
        """Type-safe access to away team stats.

        Returns:
            TeamStats instance with structured team data
        """
        return TeamStats.from_dict(self.away_team)

    @property
    def game_player_stats(self) -> GamePlayerStats:
        """Type-safe access to player stats.

        Returns:
            GamePlayerStats instance with home/away player lists
        """
        return GamePlayerStats.from_dict(self.player_stats)

    @property
    def event_type(self) -> str:
        return EventTypes.GAME_UPDATE.value
