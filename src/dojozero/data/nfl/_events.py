"""NFL-specific event types."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dojozero.data._models import DataEvent, register_event


# =============================================================================
# Supporting Dataclasses (not events, just data containers)
# =============================================================================


@dataclass(slots=True, frozen=True)
class NFLTeamStats:
    """NFL team statistics within a game.

    Contains passing, rushing, and other team-level stats.
    """

    team_id: str = field(default="")
    team_name: str = field(default="")
    team_abbreviation: str = field(default="")
    score: int = field(default=0)
    # Offensive stats
    total_yards: int = field(default=0)
    passing_yards: int = field(default=0)
    rushing_yards: int = field(default=0)
    first_downs: int = field(default=0)
    # Turnover stats
    turnovers: int = field(default=0)
    fumbles_lost: int = field(default=0)
    interceptions_thrown: int = field(default=0)
    # Time of possession (in seconds)
    time_of_possession: int = field(default=0)
    # Penalty stats
    penalties: int = field(default=0)
    penalty_yards: int = field(default=0)
    # Third/fourth down efficiency
    third_down_conversions: int = field(default=0)
    third_down_attempts: int = field(default=0)
    fourth_down_conversions: int = field(default=0)
    fourth_down_attempts: int = field(default=0)
    # Red zone
    red_zone_attempts: int = field(default=0)
    red_zone_conversions: int = field(default=0)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NFLTeamStats":
        """Create NFLTeamStats from API dict."""
        # ESPN statistics come as a list of {name, displayValue} objects
        stats_dict: dict[str, Any] = {}
        statistics = data.get("statistics", [])
        if isinstance(statistics, list):
            for stat in statistics:
                if isinstance(stat, dict):
                    name = stat.get("name", "")
                    value = stat.get("displayValue", "0")
                    stats_dict[name] = value

        def parse_int(val: Any) -> int:
            try:
                return int(str(val).split("/")[0])  # Handle "X/Y" format
            except (ValueError, TypeError):
                return 0

        def parse_time(val: str) -> int:
            """Parse time of possession (MM:SS) to seconds."""
            try:
                parts = str(val).split(":")
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
                return 0
            except (ValueError, TypeError):
                return 0

        team_data = data.get("team", {})

        return cls(
            team_id=str(team_data.get("id", "")),
            team_name=team_data.get("displayName", ""),
            team_abbreviation=team_data.get("abbreviation", ""),
            score=parse_int(data.get("score", 0)),
            total_yards=parse_int(stats_dict.get("totalYards", 0)),
            passing_yards=parse_int(stats_dict.get("netPassingYards", 0)),
            rushing_yards=parse_int(stats_dict.get("rushingYards", 0)),
            first_downs=parse_int(stats_dict.get("firstDowns", 0)),
            turnovers=parse_int(stats_dict.get("turnovers", 0)),
            fumbles_lost=parse_int(stats_dict.get("fumblesLost", 0)),
            interceptions_thrown=parse_int(stats_dict.get("interceptions", 0)),
            time_of_possession=parse_time(stats_dict.get("possessionTime", "0:00")),
            penalties=parse_int(
                stats_dict.get("totalPenaltiesYards", "0").split("-")[0]
            )
            if "-" in str(stats_dict.get("totalPenaltiesYards", "0"))
            else 0,
            penalty_yards=parse_int(
                stats_dict.get("totalPenaltiesYards", "0-0").split("-")[-1]
            ),
            third_down_conversions=parse_int(
                stats_dict.get("thirdDownEff", "0/0").split("/")[0]
            ),
            third_down_attempts=parse_int(
                stats_dict.get("thirdDownEff", "0/0").split("/")[-1]
            ),
            fourth_down_conversions=parse_int(
                stats_dict.get("fourthDownEff", "0/0").split("/")[0]
            ),
            fourth_down_attempts=parse_int(
                stats_dict.get("fourthDownEff", "0/0").split("/")[-1]
            ),
            red_zone_attempts=parse_int(
                stats_dict.get("redZoneAttempts", "0/0").split("/")[-1]
            ),
            red_zone_conversions=parse_int(
                stats_dict.get("redZoneAttempts", "0/0").split("/")[0]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "teamId": self.team_id,
            "teamName": self.team_name,
            "teamAbbreviation": self.team_abbreviation,
            "score": self.score,
            "totalYards": self.total_yards,
            "passingYards": self.passing_yards,
            "rushingYards": self.rushing_yards,
            "firstDowns": self.first_downs,
            "turnovers": self.turnovers,
            "fumblesLost": self.fumbles_lost,
            "interceptionsThrown": self.interceptions_thrown,
            "timeOfPossession": self.time_of_possession,
            "penalties": self.penalties,
            "penaltyYards": self.penalty_yards,
            "thirdDownConversions": self.third_down_conversions,
            "thirdDownAttempts": self.third_down_attempts,
            "fourthDownConversions": self.fourth_down_conversions,
            "fourthDownAttempts": self.fourth_down_attempts,
            "redZoneAttempts": self.red_zone_attempts,
            "redZoneConversions": self.red_zone_conversions,
        }


@dataclass(slots=True, frozen=True)
class NFLPlayerStats:
    """NFL player statistics within a game."""

    player_id: str = field(default="")
    name: str = field(default="")
    position: str = field(default="")
    team_id: str = field(default="")
    # Passing stats
    passing_completions: int = field(default=0)
    passing_attempts: int = field(default=0)
    passing_yards: int = field(default=0)
    passing_touchdowns: int = field(default=0)
    interceptions: int = field(default=0)
    # Rushing stats
    rushing_attempts: int = field(default=0)
    rushing_yards: int = field(default=0)
    rushing_touchdowns: int = field(default=0)
    # Receiving stats
    receptions: int = field(default=0)
    receiving_yards: int = field(default=0)
    receiving_touchdowns: int = field(default=0)
    targets: int = field(default=0)
    # Defense stats
    tackles: int = field(default=0)
    sacks: float = field(default=0.0)
    interceptions_defense: int = field(default=0)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NFLPlayerStats":
        """Create NFLPlayerStats from API dict."""

        def parse_int(val: Any) -> int:
            try:
                return int(val)
            except (ValueError, TypeError):
                return 0

        def parse_float(val: Any) -> float:
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        athlete = data.get("athlete", {})
        stats = data.get("stats", [])

        return cls(
            player_id=str(athlete.get("id", "")),
            name=athlete.get("displayName", ""),
            position=athlete.get("position", {}).get("abbreviation", ""),
            team_id=str(data.get("teamId", "")),
            passing_completions=parse_int(stats[0]) if len(stats) > 0 else 0,
            passing_attempts=parse_int(stats[1]) if len(stats) > 1 else 0,
            passing_yards=parse_int(stats[2]) if len(stats) > 2 else 0,
            passing_touchdowns=parse_int(stats[4]) if len(stats) > 4 else 0,
            interceptions=parse_int(stats[5]) if len(stats) > 5 else 0,
            rushing_attempts=parse_int(stats[0]) if len(stats) > 0 else 0,
            rushing_yards=parse_int(stats[1]) if len(stats) > 1 else 0,
            rushing_touchdowns=parse_int(stats[3]) if len(stats) > 3 else 0,
            receptions=parse_int(stats[0]) if len(stats) > 0 else 0,
            receiving_yards=parse_int(stats[1]) if len(stats) > 1 else 0,
            receiving_touchdowns=parse_int(stats[3]) if len(stats) > 3 else 0,
            targets=parse_int(stats[4]) if len(stats) > 4 else 0,
            tackles=parse_int(stats[0]) if len(stats) > 0 else 0,
            sacks=parse_float(stats[1]) if len(stats) > 1 else 0.0,
            interceptions_defense=parse_int(stats[2]) if len(stats) > 2 else 0,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "playerId": self.player_id,
            "name": self.name,
            "position": self.position,
            "teamId": self.team_id,
            "passingCompletions": self.passing_completions,
            "passingAttempts": self.passing_attempts,
            "passingYards": self.passing_yards,
            "passingTouchdowns": self.passing_touchdowns,
            "interceptions": self.interceptions,
            "rushingAttempts": self.rushing_attempts,
            "rushingYards": self.rushing_yards,
            "rushingTouchdowns": self.rushing_touchdowns,
            "receptions": self.receptions,
            "receivingYards": self.receiving_yards,
            "receivingTouchdowns": self.receiving_touchdowns,
            "targets": self.targets,
            "tackles": self.tackles,
            "sacks": self.sacks,
            "interceptionsDefense": self.interceptions_defense,
        }


# =============================================================================
# Game Lifecycle Events
# =============================================================================


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class NFLGameInitializeEvent(DataEvent):
    """NFL game initialization event with team information.

    Emitted when a game is first detected, providing basic info needed
    to initialize a betting event.
    """

    event_id: str = field(default="")  # ESPN event ID
    home_team: str = field(default="")  # Full team name (e.g., "Kansas City Chiefs")
    away_team: str = field(default="")  # Full team name (e.g., "San Francisco 49ers")
    home_team_id: str = field(default="")
    away_team_id: str = field(default="")
    home_team_abbreviation: str = field(default="")  # e.g., "KC"
    away_team_abbreviation: str = field(default="")  # e.g., "SF"
    venue: str = field(default="")  # Stadium name
    game_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    week: int = field(default=0)  # NFL week number
    season_type: int = field(default=2)  # 1=preseason, 2=regular, 3=postseason

    @property
    def event_type(self) -> str:
        return "nfl_game_initialize"


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class NFLGameStartEvent(DataEvent):
    """NFL game start event signaling kickoff."""

    event_id: str = field(default="")

    @property
    def event_type(self) -> str:
        return "nfl_game_start"


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class NFLGameResultEvent(DataEvent):
    """NFL game result event with winner and final score."""

    event_id: str = field(default="")
    winner: str = field(default="")  # "home" or "away" or "" for tie
    final_score: dict[str, int] = field(
        default_factory=dict
    )  # {"home": 31, "away": 20}
    home_team: str = field(default="")
    away_team: str = field(default="")

    @property
    def event_type(self) -> str:
        return "nfl_game_result"


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class NFLGameUpdateEvent(DataEvent):
    """NFL game update event with boxscore snapshot.

    Contains team stats and current game state.
    """

    event_id: str = field(default="")
    quarter: int = field(default=0)  # 1-4 for regulation, 5+ for OT
    game_clock: str = field(default="")  # e.g., "12:34"
    possession: str = field(default="")  # Team abbreviation with possession
    down: int = field(default=0)  # 1-4
    distance: int = field(default=0)  # Yards to first down
    yard_line: str = field(default="")  # e.g., "KC 25"
    home_team: dict[str, Any] = field(default_factory=dict)  # NFLTeamStats as dict
    away_team: dict[str, Any] = field(default_factory=dict)  # NFLTeamStats as dict
    # Quarter scores
    home_line_scores: list[int] = field(default_factory=list)  # [7, 3, 14, 7]
    away_line_scores: list[int] = field(default_factory=list)  # [0, 10, 7, 3]

    @property
    def event_type(self) -> str:
        return "nfl_game_update"

    @property
    def home_team_stats(self) -> NFLTeamStats:
        """Type-safe access to home team stats."""
        return NFLTeamStats.from_dict(self.home_team)

    @property
    def away_team_stats(self) -> NFLTeamStats:
        """Type-safe access to away team stats."""
        return NFLTeamStats.from_dict(self.away_team)


# =============================================================================
# Play-by-Play Events
# =============================================================================


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class NFLPlayEvent(DataEvent):
    """Single NFL play event.

    Contains detailed information about a single play.
    """

    event_id: str = field(default="")  # ESPN event ID
    play_id: str = field(default="")  # Unique play ID
    sequence_number: int = field(default=0)  # Play sequence in game
    quarter: int = field(default=0)
    game_clock: str = field(default="")  # e.g., "12:34"
    down: int = field(default=0)  # 1-4, or 0 for non-scrimmage plays
    distance: int = field(default=0)  # Yards to first down
    yard_line: int = field(default=0)  # Absolute yard line (0-100)
    play_type: str = field(default="")  # e.g., "Pass", "Rush", "Punt", "Field Goal"
    description: str = field(default="")  # Full play description
    yards_gained: int = field(default=0)
    is_scoring_play: bool = field(default=False)
    score_value: int = field(default=0)  # Points scored on this play
    home_score: int = field(default=0)
    away_score: int = field(default=0)
    team_id: str = field(default="")  # Team with possession
    team_abbreviation: str = field(default="")
    is_turnover: bool = field(default=False)

    @property
    def event_type(self) -> str:
        return "nfl_play"


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class NFLDriveEvent(DataEvent):
    """NFL drive summary event.

    Contains information about a complete drive.
    """

    event_id: str = field(default="")  # ESPN event ID
    drive_id: str = field(default="")  # Unique drive ID
    drive_number: int = field(default=0)  # Drive sequence in game
    team_id: str = field(default="")  # Driving team
    team_abbreviation: str = field(default="")
    # Drive start
    start_quarter: int = field(default=0)
    start_clock: str = field(default="")
    start_yard_line: int = field(default=0)
    # Drive end
    end_quarter: int = field(default=0)
    end_clock: str = field(default="")
    end_yard_line: int = field(default=0)
    # Drive stats
    plays: int = field(default=0)  # Number of plays
    yards: int = field(default=0)  # Total yards gained
    time_elapsed: str = field(default="")  # e.g., "4:32"
    # Result
    result: str = field(
        default=""
    )  # e.g., "Touchdown", "Field Goal", "Punt", "Turnover"
    is_score: bool = field(default=False)
    points_scored: int = field(default=0)

    @property
    def event_type(self) -> str:
        return "nfl_drive"


# =============================================================================
# Odds Events
# =============================================================================


@register_event
@dataclass(slots=True, frozen=True, kw_only=True)
class NFLOddsUpdateEvent(DataEvent):
    """NFL betting odds update event.

    Contains spread, over/under, and moneyline odds from sportsbooks.
    """

    event_id: str = field(default="")  # ESPN event ID
    provider: str = field(default="")  # e.g., "Draft Kings", "FanDuel"
    # Spread betting
    spread: float = field(default=0.0)  # Positive = home team favored
    spread_odds_home: int = field(default=-110)  # American odds for home spread
    spread_odds_away: int = field(default=-110)  # American odds for away spread
    # Over/Under
    over_under: float = field(default=0.0)  # Total points line
    over_odds: int = field(default=-110)
    under_odds: int = field(default=-110)
    # Moneyline
    moneyline_home: int = field(default=0)  # American odds for home win
    moneyline_away: int = field(default=0)  # American odds for away win
    # Metadata
    home_team: str = field(default="")
    away_team: str = field(default="")

    @property
    def event_type(self) -> str:
        return "nfl_odds_update"
