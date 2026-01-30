"""ESPN stats-based insight event types.

These are StatsInsightEvent subclasses that provide pre-game intelligence
derived from ESPN stats API endpoints.

Hierarchy:
    PreGameInsightEvent
    └── StatsInsightEvent (home_team_id, away_team_id, season)
        ├── HeadToHeadEvent (historical matchup record)
        ├── TeamStatsEvent (team season stats)
        ├── PlayerStatsEvent (key player stats)
        └── RecentFormEvent (last N game results)
"""

from typing import Any, Literal

from pydantic import Field

from dojozero.data._models import (
    StatsInsightEvent,
    register_event,
)


@register_event
class HeadToHeadEvent(StatsInsightEvent):
    """Historical head-to-head matchup record between two teams.

    Derived from team schedule data by filtering past matchups.
    No dedicated ESPN H2H endpoint exists, so this is computed
    from ``site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{id}/schedule``.
    """

    event_type: Literal["event.head_to_head"] = "event.head_to_head"

    total_games: int = 0
    home_wins: int = 0
    away_wins: int = 0
    last_n_games: int = 0  # how many recent games are included
    games: list[dict[str, Any]] = Field(default_factory=list)


@register_event
class TeamStatsEvent(StatsInsightEvent):
    """Team season statistics snapshot.

    Stats sourced from:
    ``sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/seasons/{year}/types/{type}/teams/{id}/statistics``
    """

    event_type: Literal["event.team_stats"] = "event.team_stats"

    team_id: str = ""
    team_name: str = ""
    stats: dict[str, Any] = Field(default_factory=dict)
    rank: dict[str, int] = Field(default_factory=dict)


@register_event
class PlayerStatsEvent(StatsInsightEvent):
    """Key player statistics for a team.

    Player data sourced from:
    - Roster: ``site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{id}/roster``
    - Stats:  ``site.web.api.espn.com/apis/common/v3/sports/{sport}/{league}/athletes/{id}/overview``
    """

    event_type: Literal["event.player_stats"] = "event.player_stats"

    team_id: str = ""
    team_name: str = ""
    players: list[dict[str, Any]] = Field(default_factory=list)


@register_event
class RecentFormEvent(StatsInsightEvent):
    """Recent form (last N games) for a team.

    Derived from team schedule data:
    ``site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{id}/schedule``
    """

    event_type: Literal["event.recent_form"] = "event.recent_form"

    team_id: str = ""
    team_name: str = ""
    last_n: int = 10
    wins: int = 0
    losses: int = 0
    streak: str = ""  # e.g., "W3", "L2"
    games: list[dict[str, Any]] = Field(default_factory=list)
    avg_points_scored: float = 0.0
    avg_points_allowed: float = 0.0


__all__ = [
    "HeadToHeadEvent",
    "PlayerStatsEvent",
    "RecentFormEvent",
    "TeamStatsEvent",
]
