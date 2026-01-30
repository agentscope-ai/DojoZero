"""ESPN stats-based pre-game insight event.

A single PreGameStatsEvent bundles all pre-game statistical intelligence
derived from ESPN API endpoints into one event with embedded value objects.

Hierarchy:
    PreGameInsightEvent
    └── StatsInsightEvent (home_team_id, away_team_id, season)
        └── PreGameStatsEvent (all stats sections)
"""

from typing import Literal

from dojozero.data._models import (
    HomeAwaySplits,
    ScheduleDensity,
    SeasonSeries,
    StatsInsightEvent,
    TeamPlayerStats,
    TeamRecentForm,
    TeamSeasonStats,
    TeamStandings,
    register_event,
)


@register_event
class PreGameStatsEvent(StatsInsightEvent):
    """Unified pre-game statistics event.

    All sections are optional so that partial data (e.g., standings fetch
    failed but schedule succeeded) can still be delivered to agents.

    Data sourced from ESPN API endpoints:
    - Team schedule: season series, recent form, schedule density, home/away splits
    - Team statistics: season averages and league ranks
    - Standings: conference and division rankings
    - Team roster: key player stats
    """

    event_type: Literal["event.pregame_stats"] = "event.pregame_stats"

    # Season series (H2H this season)
    season_series: SeasonSeries | None = None

    # Recent form per team
    home_recent_form: TeamRecentForm | None = None
    away_recent_form: TeamRecentForm | None = None

    # Schedule density per team
    home_schedule: ScheduleDensity | None = None
    away_schedule: ScheduleDensity | None = None

    # Team season stats
    home_team_stats: TeamSeasonStats | None = None
    away_team_stats: TeamSeasonStats | None = None

    # Home/away splits
    home_splits: HomeAwaySplits | None = None
    away_splits: HomeAwaySplits | None = None

    # Key player stats
    home_players: TeamPlayerStats | None = None
    away_players: TeamPlayerStats | None = None

    # Standings
    home_standings: TeamStandings | None = None
    away_standings: TeamStandings | None = None


__all__ = [
    "PreGameStatsEvent",
]
