"""NCAA utility functions for game info and team data.

Reuses the NBA utility infrastructure since ESPN API structure is the same
for college basketball, just with different sport/league parameters.
"""

import logging
from typing import Any

from dojozero.data._game_info import GameInfo, TeamInfo
from dojozero.data.espn import ESPNExternalAPI

logger = logging.getLogger(__name__)


async def get_game_info_by_id_async(
    game_id: str,
    proxy: str | None = None,
) -> GameInfo | None:
    """Fetch NCAA game info by ESPN event ID.

    Args:
        game_id: ESPN event ID (e.g., "401522202")
        proxy: Optional proxy URL

    Returns:
        GameInfo or None if not found
    """
    api = ESPNExternalAPI(
        sport="basketball",
        league="mens-college-basketball",
        proxy=proxy,
    )
    try:
        data = await api.fetch("summary", {"event": game_id})
        return _extract_game_info_from_summary(data, game_id)
    except Exception as e:
        logger.error("Error fetching NCAA game info for %s: %s", game_id, e)
        return None
    finally:
        await api.close()


def _extract_game_info_from_summary(
    data: dict[str, Any], game_id: str
) -> GameInfo | None:
    """Extract GameInfo from ESPN summary response."""
    header = data.get("header", {})
    competitions = header.get("competitions", [])
    if not competitions:
        return None

    competition = competitions[0]
    competitors = competition.get("competitors", [])
    if len(competitors) < 2:
        return None

    home_comp = None
    away_comp = None
    for comp in competitors:
        if comp.get("homeAway") == "home":
            home_comp = comp
        elif comp.get("homeAway") == "away":
            away_comp = comp

    if not home_comp or not away_comp:
        return None

    def _build_team(comp: dict[str, Any]) -> TeamInfo:
        team = comp.get("team", {})
        logos = team.get("logos", [])
        return TeamInfo.model_validate(
            {
                "teamId": str(team.get("id", "")),
                "displayName": team.get("displayName", ""),
                "teamTricode": team.get("abbreviation", ""),
                "logo": logos[0].get("href", "") if logos else "",
            }
        )

    # Extract game time
    game_time_str = competition.get("date", "")
    game_time_utc = None
    if game_time_str:
        from dojozero.data.nba._utils import parse_iso_datetime

        game_time_utc = parse_iso_datetime(game_time_str)

    # Extract status
    status_data = competition.get("status", {})
    status_type = status_data.get("type", {})
    status = int(status_type.get("id", 1))
    status_text = str(status_type.get("shortDetail", ""))

    # Extract season info
    season_data = header.get("season", {})
    season_year = season_data.get("year", 0)
    season_type = season_data.get("type", 0)

    home_team = _build_team(home_comp)
    away_team = _build_team(away_comp)

    return GameInfo.model_validate(
        {
            "gameId": game_id,
            "sport_type": "ncaa",
            "homeTeam": home_team,
            "awayTeam": away_team,
            "gameTimeUTC": game_time_utc,
            "gameStatus": status,
            "gameStatusText": status_text,
            "shortName": f"{away_team.tricode} @ {home_team.tricode}",
            "seasonYear": season_year,
            "seasonType": season_type,
        }
    )


__all__ = ["get_game_info_by_id_async"]
