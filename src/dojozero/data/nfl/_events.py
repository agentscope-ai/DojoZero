"""NFL-specific event types.

NFL uses all three tiers of the event hierarchy:
- Atomic (Tier 1): NFLPlayEvent — individual plays
- Segment (Tier 2): NFLDriveEvent — completed drives
- Snapshot (Tier 3): NFLGameUpdateEvent — boxscore snapshots

Lifecycle events (GameInitializeEvent, GameStartEvent, GameResultEvent)
and OddsUpdateEvent are unified across sports and defined in _models.py.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dojozero.data._models import (
    BaseGameUpdateEvent,
    BasePlayEvent,
    BaseSegmentEvent,
    register_event,
)


# =============================================================================
# NFL Stats Models (Pydantic, frozen)
# =============================================================================


class NFLTeamGameStats(BaseModel):
    """NFL team statistics within a game.

    Contains passing, rushing, and other team-level stats.
    """

    model_config = ConfigDict(frozen=True)

    team_id: str = ""
    team_name: str = ""
    team_abbreviation: str = ""
    score: int = 0
    # Offensive stats
    total_yards: int = 0
    passing_yards: int = 0
    rushing_yards: int = 0
    first_downs: int = 0
    # Turnover stats
    turnovers: int = 0
    fumbles_lost: int = 0
    interceptions_thrown: int = 0
    # Time of possession (in seconds)
    time_of_possession: int = 0
    # Penalty stats
    penalties: int = 0
    penalty_yards: int = 0
    # Third/fourth down efficiency
    third_down_conversions: int = 0
    third_down_attempts: int = 0
    fourth_down_conversions: int = 0
    fourth_down_attempts: int = 0
    # Red zone
    red_zone_attempts: int = 0
    red_zone_conversions: int = 0

    @classmethod
    def from_espn_api(cls, data: dict[str, Any]) -> "NFLTeamGameStats":
        """Create NFLTeamGameStats from ESPN API competitor dict.

        ESPN statistics come as a list of {name, displayValue} objects.
        This method handles the ESPN-specific parsing.
        """
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
                return int(str(val).split("/")[0].split("-")[0])
            except (ValueError, TypeError):
                return 0

        def _split_eff(val: str) -> tuple[int, int]:
            """Split efficiency stat like '7-13' or '7/13' into (made, attempts)."""
            s = str(val)
            for sep in ("-", "/"):
                if sep in s:
                    parts = s.split(sep)
                    try:
                        return int(parts[0]), int(parts[-1])
                    except (ValueError, TypeError):
                        pass
            return 0, 0

        def parse_time(val: str) -> int:
            try:
                parts = str(val).split(":")
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
                return 0
            except (ValueError, TypeError):
                return 0

        team_data = data.get("team", {})

        # Handle penalties in "X-Y" format
        penalties_str = str(stats_dict.get("totalPenaltiesYards", "0"))
        if "-" in penalties_str:
            pen_parts = penalties_str.split("-")
            penalties_count = parse_int(pen_parts[0])
            penalties_yards = parse_int(pen_parts[-1])
        else:
            penalties_count = 0
            penalties_yards = 0

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
            penalties=penalties_count,
            penalty_yards=penalties_yards,
            third_down_conversions=_split_eff(stats_dict.get("thirdDownEff", "0-0"))[0],
            third_down_attempts=_split_eff(stats_dict.get("thirdDownEff", "0-0"))[1],
            fourth_down_conversions=_split_eff(stats_dict.get("fourthDownEff", "0-0"))[
                0
            ],
            fourth_down_attempts=_split_eff(stats_dict.get("fourthDownEff", "0-0"))[1],
            red_zone_conversions=_split_eff(stats_dict.get("redZoneAttempts", "0-0"))[
                0
            ],
            red_zone_attempts=_split_eff(stats_dict.get("redZoneAttempts", "0-0"))[1],
        )


class NFLPlayerStats(BaseModel):
    """NFL player statistics within a game."""

    model_config = ConfigDict(frozen=True)

    player_id: str = ""
    name: str = ""
    position: str = ""
    team_id: str = ""
    # Passing stats
    passing_completions: int = 0
    passing_attempts: int = 0
    passing_yards: int = 0
    passing_touchdowns: int = 0
    interceptions: int = 0
    # Rushing stats
    rushing_attempts: int = 0
    rushing_yards: int = 0
    rushing_touchdowns: int = 0
    # Receiving stats
    receptions: int = 0
    receiving_yards: int = 0
    receiving_touchdowns: int = 0
    targets: int = 0
    # Defense stats
    tackles: int = 0
    sacks: float = 0.0
    interceptions_defense: int = 0


# =============================================================================
# Tier 1: Atomic — NFLPlayEvent
# =============================================================================


@register_event
class NFLPlayEvent(BasePlayEvent):
    """NFL play-by-play event.

    Extends BasePlayEvent with NFL-specific fields.
    """

    down: int = 0  # 1-4, or 0 for non-scrimmage plays
    distance: int = 0  # Yards to first down
    yard_line: int = 0  # Absolute yard line (0-100)
    play_type: str = ""  # "Pass", "Rush", "Punt", "Field Goal"
    yards_gained: int = 0
    is_turnover: bool = False

    event_type: Literal["event.nfl_play"] = "event.nfl_play"

    # NFL uses team_abbreviation instead of team_tricode
    team_abbreviation: str = ""

    def get_dedup_key(self) -> str | None:
        """Return dedup key for NFL play-by-play events."""
        if self.game_id and self.play_id:
            return f"{self.game_id}_play_{self.play_id}"
        return None


# =============================================================================
# Tier 2: Segment — NFLDriveEvent
# =============================================================================


@register_event
class NFLDriveEvent(BaseSegmentEvent):
    """NFL drive summary event.

    Extends BaseSegmentEvent with NFL drive-specific fields.
    """

    drive_id: str = ""
    drive_number: int = 0
    yards: int = 0  # Total yards gained on drive
    time_elapsed: str = ""  # e.g., "4:32"

    event_type: Literal["event.nfl_drive"] = "event.nfl_drive"

    # Drive start/end yard lines
    start_yard_line: int = 0
    end_yard_line: int = 0

    def get_dedup_key(self) -> str | None:
        """Return dedup key for NFL drive events."""
        if self.game_id and self.drive_id:
            return f"{self.game_id}_drive_{self.drive_id}"
        return None


# =============================================================================
# Tier 3: Snapshot — NFLGameUpdateEvent
# =============================================================================


@register_event
class NFLGameUpdateEvent(BaseGameUpdateEvent):
    """NFL game update with boxscore snapshot.

    Contains team stats as structured Pydantic models (not raw dicts).
    """

    possession: str = ""  # Team abbreviation with possession
    down: int = 0  # 1-4
    distance: int = 0  # Yards to first down
    yard_line: int = 0  # Absolute yard line (0-100), 0 = home goal, 100 = away goal
    home_team_stats: NFLTeamGameStats = Field(default_factory=NFLTeamGameStats)
    away_team_stats: NFLTeamGameStats = Field(default_factory=NFLTeamGameStats)
    event_type: Literal["event.nfl_game_update"] = "event.nfl_game_update"

    # Quarter scores
    home_line_scores: list[int] = Field(default_factory=list)
    away_line_scores: list[int] = Field(default_factory=list)


__all__ = [
    "NFLDriveEvent",
    "NFLGameUpdateEvent",
    "NFLPlayEvent",
    "NFLPlayerStats",
    "NFLTeamGameStats",
]
