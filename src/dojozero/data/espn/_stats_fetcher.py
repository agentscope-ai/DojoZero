"""Pre-game stats fetcher: ESPN API → PreGameStatsEvent.

Fetches team schedules, statistics, standings, and rosters from ESPN in
parallel, then assembles a single PreGameStatsEvent with all sections.

Each section is independently fault-tolerant — a failed API call produces
``None`` for that section rather than failing the entire event.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from dojozero.data._models import (
    HomeAwaySplits,
    ScheduleDensity,
    SeasonSeries,
    TeamPlayerStats,
    TeamRecentForm,
    TeamSeasonStats,
    TeamStandings,
)
from dojozero.data.espn._api import ESPNExternalAPI
from dojozero.data.espn._stats_events import PreGameStatsEvent
from dojozero.data.espn._utils import safe_score

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def fetch_pregame_stats(
    api: ESPNExternalAPI,
    *,
    home_team_id: str,
    away_team_id: str,
    game_id: str,
    game_date: str,
    sport: str,
    season_year: int,
    season_type: str,
    home_team_name: str = "",
    away_team_name: str = "",
) -> PreGameStatsEvent:
    """Fetch all pre-game stats from ESPN and build a PreGameStatsEvent.

    Runs up to 7 API calls in parallel (schedule × 2, statistics × 2,
    standings × 1, roster × 2). Each section is wrapped in try/except so
    partial data is returned on failure.

    Args:
        api: ESPNExternalAPI instance (basketball/nba or football/nfl).
        home_team_id: ESPN team ID for the home team.
        away_team_id: ESPN team ID for the away team.
        game_id: ESPN event/game ID.
        game_date: Game date in YYYY-MM-DD format.
        sport: Sport identifier ("nba", "nfl").
        season_year: Season year (e.g., 2025).
        season_type: Season type ("regular", "postseason", "preseason").
        home_team_name: Full home team name (for display).
        away_team_name: Full away team name (for display).

    Returns:
        PreGameStatsEvent with all available sections populated.
    """
    logger.info(
        "Fetching pregame stats for %s vs %s (game_id=%s, date=%s)",
        away_team_name or away_team_id,
        home_team_name or home_team_id,
        game_id,
        game_date,
    )

    # Fire all API calls concurrently (9 calls)
    leader_params_home = {
        "team_id": home_team_id,
        "season_year": season_year,
        "season_type": season_type,
    }
    leader_params_away = {
        "team_id": away_team_id,
        "season_year": season_year,
        "season_type": season_type,
    }
    (
        home_schedule_raw,
        away_schedule_raw,
        home_stats_raw,
        away_stats_raw,
        standings_raw,
        home_roster_raw,
        away_roster_raw,
        home_leaders_raw,
        away_leaders_raw,
    ) = await asyncio.gather(
        _safe_fetch(
            api, "team_schedule", {"team_id": home_team_id, "season": season_year}
        ),
        _safe_fetch(
            api, "team_schedule", {"team_id": away_team_id, "season": season_year}
        ),
        _safe_fetch(
            api,
            "team_statistics",
            {
                "team_id": home_team_id,
                "season_year": season_year,
                "season_type": season_type,
            },
        ),
        _safe_fetch(
            api,
            "team_statistics",
            {
                "team_id": away_team_id,
                "season_year": season_year,
                "season_type": season_type,
            },
        ),
        _safe_fetch(api, "standings", {"season": season_year}),
        _safe_fetch(api, "team_roster", {"team_id": home_team_id}),
        _safe_fetch(api, "team_roster", {"team_id": away_team_id}),
        _safe_fetch(api, "team_leaders", leader_params_home),
        _safe_fetch(api, "team_leaders", leader_params_away),
    )

    # Parse each section independently
    season_series = _parse_season_series(
        home_schedule_raw, home_team_id, away_team_id, home_team_name, away_team_name
    )
    home_recent_form = _parse_recent_form(
        home_schedule_raw, home_team_id, home_team_name, game_date
    )
    away_recent_form = _parse_recent_form(
        away_schedule_raw, away_team_id, away_team_name, game_date
    )
    home_schedule = _parse_schedule_density(
        home_schedule_raw, home_team_id, home_team_name, game_date
    )
    away_schedule = _parse_schedule_density(
        away_schedule_raw, away_team_id, away_team_name, game_date
    )
    home_team_stats = _parse_team_statistics(
        home_stats_raw, home_team_id, home_team_name
    )
    away_team_stats = _parse_team_statistics(
        away_stats_raw, away_team_id, away_team_name
    )
    home_splits = _parse_home_away_splits(
        home_schedule_raw, home_team_id, home_team_name, game_date
    )
    away_splits = _parse_home_away_splits(
        away_schedule_raw, away_team_id, away_team_name, game_date
    )
    home_players = _parse_player_stats(
        home_roster_raw, home_leaders_raw, home_team_id, home_team_name
    )
    away_players = _parse_player_stats(
        away_roster_raw, away_leaders_raw, away_team_id, away_team_name
    )
    home_standings = _parse_standings(standings_raw, home_team_id, home_team_name)
    away_standings = _parse_standings(standings_raw, away_team_id, away_team_name)

    populated = sum(
        1
        for s in [
            season_series,
            home_recent_form,
            away_recent_form,
            home_schedule,
            away_schedule,
            home_team_stats,
            away_team_stats,
            home_splits,
            away_splits,
            home_players,
            away_players,
            home_standings,
            away_standings,
        ]
        if s is not None
    )
    logger.info("Pregame stats assembled: %d/13 sections populated", populated)

    return PreGameStatsEvent(
        game_id=game_id,
        sport=sport,
        source="espn_stats",
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        season_year=season_year,
        season_type=season_type,
        season_series=season_series,
        home_recent_form=home_recent_form,
        away_recent_form=away_recent_form,
        home_schedule=home_schedule,
        away_schedule=away_schedule,
        home_team_stats=home_team_stats,
        away_team_stats=away_team_stats,
        home_splits=home_splits,
        away_splits=away_splits,
        home_players=home_players,
        away_players=away_players,
        home_standings=home_standings,
        away_standings=away_standings,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _safe_fetch(
    api: ESPNExternalAPI, endpoint: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Fetch an ESPN endpoint, returning empty dict on any error."""
    try:
        return await api.fetch(endpoint, params)
    except Exception as e:
        logger.warning("ESPN fetch failed (%s): %s", endpoint, e)
        return {}


def _get_completed_events(raw: dict[str, Any], game_date: str) -> list[dict[str, Any]]:
    """Extract completed game events from a team schedule response.

    Only includes games before ``game_date`` that have a final status.
    """
    schedule = raw.get("team_schedule", {})
    events: list[dict[str, Any]] = schedule.get("events", [])
    completed = []
    for ev in events:
        # Skip events on or after game_date
        ev_date = ev.get("date", "")[:10]  # "2025-01-15T..."  → "2025-01-15"
        if ev_date >= game_date:
            continue
        # Only include completed games (status.type.completed == true)
        status = (
            ev.get("competitions", [{}])[0].get("status", {})
            if ev.get("competitions")
            else {}
        )
        status_type = status.get("type", {})
        if status_type.get("completed", False):
            completed.append(ev)
    return completed


def _get_competitor_info(
    competition: dict[str, Any], team_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (team_competitor, opponent_competitor) from a competition."""
    competitors = competition.get("competitors", [])
    team_comp = None
    opp_comp = None
    for c in competitors:
        cid = str(c.get("id", ""))
        if cid == str(team_id):
            team_comp = c
        else:
            opp_comp = c
    return team_comp, opp_comp


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_season_series(
    home_schedule_raw: dict[str, Any],
    home_team_id: str,
    away_team_id: str,
    home_team_name: str,  # noqa: ARG001
    away_team_name: str,  # noqa: ARG001
) -> SeasonSeries | None:
    """Parse season series (H2H this season) from home team schedule."""
    try:
        schedule = home_schedule_raw.get("team_schedule", {})
        events: list[dict[str, Any]] = schedule.get("events", [])

        games: list[dict[str, Any]] = []
        home_wins = 0
        away_wins = 0

        for ev in events:
            comps = ev.get("competitions", [])
            if not comps:
                continue
            comp = comps[0]

            # Check if the opponent is the away team
            team_comp, opp_comp = _get_competitor_info(comp, home_team_id)
            if not opp_comp or str(opp_comp.get("id", "")) != str(away_team_id):
                continue

            # Only include completed games
            status = comp.get("status", {})
            if not status.get("type", {}).get("completed", False):
                continue

            team_score = safe_score(team_comp)
            opp_score = safe_score(opp_comp)
            winner = "home" if team_comp and team_comp.get("winner", False) else "away"

            if winner == "home":
                home_wins += 1
            else:
                away_wins += 1

            games.append(
                {
                    "date": ev.get("date", "")[:10],
                    "home_score": team_score
                    if team_comp and team_comp.get("homeAway") == "home"
                    else opp_score,
                    "away_score": opp_score
                    if team_comp and team_comp.get("homeAway") == "home"
                    else team_score,
                    "winner": winner,
                }
            )

        if not games:
            return None

        return SeasonSeries(
            total_games=len(games),
            home_wins=home_wins,
            away_wins=away_wins,
            games=games,
        )
    except Exception as e:
        logger.warning("Failed to parse season series: %s", e)
        return None


def _parse_recent_form(
    schedule_raw: dict[str, Any],
    team_id: str,
    team_name: str,
    game_date: str,
    last_n: int = 10,
) -> TeamRecentForm | None:
    """Parse recent form (last N completed games) for a team."""
    try:
        completed = _get_completed_events(schedule_raw, game_date)
        # Sort by date descending and take last N
        completed.sort(key=lambda e: e.get("date", ""), reverse=True)
        recent = completed[:last_n]

        if not recent:
            return None

        wins = 0
        losses = 0
        total_scored = 0.0
        total_allowed = 0.0
        streak_type = ""
        streak_count = 0
        games: list[dict[str, Any]] = []

        for ev in recent:
            comps = ev.get("competitions", [])
            if not comps:
                continue
            comp = comps[0]
            team_comp, opp_comp = _get_competitor_info(comp, team_id)
            if not team_comp:
                continue

            team_score = safe_score(team_comp)
            opp_score = safe_score(opp_comp)
            won = team_comp.get("winner", False)

            if won:
                wins += 1
            else:
                losses += 1

            total_scored += team_score
            total_allowed += opp_score

            # Track streak (games are date-descending)
            result = "W" if won else "L"
            if not streak_type:
                streak_type = result
                streak_count = 1
            elif result == streak_type:
                streak_count += 1
            # Stop counting streak once pattern breaks (but keep processing games)

            opp_name = ""
            if opp_comp:
                opp_team = opp_comp.get("team", {})
                opp_name = opp_team.get(
                    "displayName", opp_team.get("shortDisplayName", "")
                )

            games.append(
                {
                    "date": ev.get("date", "")[:10],
                    "opponent": opp_name,
                    "score": f"{team_score}-{opp_score}",
                    "result": result,
                    "home_away": team_comp.get("homeAway", ""),
                }
            )

        n = len(recent)
        return TeamRecentForm(
            team_id=team_id,
            team_name=team_name,
            last_n=n,
            wins=wins,
            losses=losses,
            streak=f"{streak_type}{streak_count}" if streak_type else "",
            games=games,
            avg_points_scored=round(total_scored / n, 1) if n else 0.0,
            avg_points_allowed=round(total_allowed / n, 1) if n else 0.0,
        )
    except Exception as e:
        logger.warning("Failed to parse recent form for %s: %s", team_id, e)
        return None


def _parse_schedule_density(
    schedule_raw: dict[str, Any],
    team_id: str,
    team_name: str,
    game_date: str,
) -> ScheduleDensity | None:
    """Parse schedule density (rest days, back-to-back, games in last 7/14 days)."""
    try:
        completed = _get_completed_events(schedule_raw, game_date)
        if not completed:
            return None

        # Sort by date ascending
        completed.sort(key=lambda e: e.get("date", ""))

        game_dt = datetime.strptime(game_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        # Days rest = days since last completed game
        last_game_date_str = completed[-1].get("date", "")[:10]
        if last_game_date_str:
            last_game_dt = datetime.strptime(last_game_date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            days_rest = (game_dt - last_game_dt).days
        else:
            days_rest = 0

        is_b2b = days_rest <= 1

        # Count games in last 7 and 14 days (before game_date)
        games_7 = 0
        games_14 = 0
        for ev in completed:
            ev_date_str = ev.get("date", "")[:10]
            if not ev_date_str:
                continue
            ev_dt = datetime.strptime(ev_date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            diff = (game_dt - ev_dt).days
            if 0 < diff <= 7:
                games_7 += 1
            if 0 < diff <= 14:
                games_14 += 1

        return ScheduleDensity(
            team_id=team_id,
            team_name=team_name,
            days_rest=days_rest,
            is_back_to_back=is_b2b,
            games_last_7_days=games_7,
            games_last_14_days=games_14,
        )
    except Exception as e:
        logger.warning("Failed to parse schedule density for %s: %s", team_id, e)
        return None


def _parse_team_statistics(
    stats_raw: dict[str, Any],
    team_id: str,
    team_name: str,
) -> TeamSeasonStats | None:
    """Parse team season statistics from ESPN core API response."""
    try:
        data = stats_raw.get("team_statistics", {})
        if not data:
            return None

        stats: dict[str, float] = {}
        rank: dict[str, int] = {}

        # ESPN core API: splits.categories[].stats[] with {name, value, rank}
        splits = data.get("splits", {})
        categories = splits.get("categories", [])
        for cat in categories:
            for stat in cat.get("stats", []):
                name = stat.get("name", "")
                if not name:
                    continue
                value = stat.get("value")
                if value is not None:
                    try:
                        stats[name] = float(value)
                    except (ValueError, TypeError):
                        pass
                stat_rank = stat.get("rank")
                if stat_rank is not None:
                    try:
                        rank[name] = int(stat_rank)
                    except (ValueError, TypeError):
                        pass

        if not stats:
            return None

        return TeamSeasonStats(
            team_id=team_id,
            team_name=team_name,
            stats=stats,
            rank=rank,
        )
    except Exception as e:
        logger.warning("Failed to parse team statistics for %s: %s", team_id, e)
        return None


def _parse_home_away_splits(
    schedule_raw: dict[str, Any],
    team_id: str,
    team_name: str,
    game_date: str,
) -> HomeAwaySplits | None:
    """Parse home/away record and stats from team schedule."""
    try:
        completed = _get_completed_events(schedule_raw, game_date)
        if not completed:
            return None

        home_w, home_l = 0, 0
        away_w, away_l = 0, 0
        home_scored, home_allowed, home_count = 0.0, 0.0, 0
        away_scored, away_allowed, away_count = 0.0, 0.0, 0

        for ev in completed:
            comps = ev.get("competitions", [])
            if not comps:
                continue
            comp = comps[0]
            team_comp, opp_comp = _get_competitor_info(comp, team_id)
            if not team_comp:
                continue

            team_score = safe_score(team_comp)
            opp_score = safe_score(opp_comp)
            won = team_comp.get("winner", False)
            is_home = team_comp.get("homeAway") == "home"

            if is_home:
                home_count += 1
                home_scored += team_score
                home_allowed += opp_score
                if won:
                    home_w += 1
                else:
                    home_l += 1
            else:
                away_count += 1
                away_scored += team_score
                away_allowed += opp_score
                if won:
                    away_w += 1
                else:
                    away_l += 1

        home_stats: dict[str, float] = {}
        away_stats: dict[str, float] = {}
        if home_count:
            home_stats["avg_points_scored"] = round(home_scored / home_count, 1)
            home_stats["avg_points_allowed"] = round(home_allowed / home_count, 1)
        if away_count:
            away_stats["avg_points_scored"] = round(away_scored / away_count, 1)
            away_stats["avg_points_allowed"] = round(away_allowed / away_count, 1)

        return HomeAwaySplits(
            team_id=team_id,
            team_name=team_name,
            home_record=f"{home_w}-{home_l}",
            away_record=f"{away_w}-{away_l}",
            home_stats=home_stats,
            away_stats=away_stats,
        )
    except Exception as e:
        logger.warning("Failed to parse home/away splits for %s: %s", team_id, e)
        return None


def _parse_player_stats(
    roster_raw: dict[str, Any],
    leaders_raw: dict[str, Any],
    team_id: str,
    team_name: str,
) -> TeamPlayerStats | None:
    """Parse key player stats from team roster + leaders data.

    Uses the leaders endpoint to get per-game stats (PPG, APG, RPG) per
    athlete, then merges with roster data for name/jersey/position.  Players
    are sorted by (PPG + APG + RPG) descending so the most impactful players
    appear first.
    """
    try:
        # Build roster lookup: athlete_id -> {name, jersey, position}
        roster_data = roster_raw.get("team_roster", {})
        roster_map: dict[str, dict[str, str]] = {}
        for entry in roster_data.get("athletes", []):
            if not isinstance(entry, dict):
                continue
            athletes_list: list[dict[str, Any]]
            if "displayName" in entry or "fullName" in entry:
                athletes_list = [entry]
            else:
                athletes_list = entry.get("items", [])
            for athlete in athletes_list:
                aid = str(athlete.get("id", ""))
                if not aid:
                    continue
                pos = athlete.get("position", {})
                pos_abbr = (
                    pos.get("abbreviation", "") if isinstance(pos, dict) else str(pos)
                )
                roster_map[aid] = {
                    "name": athlete.get("displayName", athlete.get("fullName", "")),
                    "jersey": str(athlete.get("jersey", "")),
                    "position": pos_abbr,
                }

        # Parse leaders: extract per-game stats keyed by athlete ID
        leaders_data = leaders_raw.get("team_leaders", {})
        categories = leaders_data.get("categories", [])

        # Map: athlete_id -> {ppg, apg, rpg, ...}
        player_stats: dict[str, dict[str, float]] = {}

        # Stat categories we care about
        stat_category_map = {
            "pointsPerGame": "ppg",
            "assistsPerGame": "apg",
            "reboundsPerGame": "rpg",
            "stealsPerGame": "spg",
            "blocksPerGame": "bpg",
            "fieldGoalPercentage": "fg_pct",
            "minutesPerGame": "mpg",
        }

        for cat in categories:
            cat_name = cat.get("name", "")
            stat_key = stat_category_map.get(cat_name)
            if not stat_key:
                continue
            for leader in cat.get("leaders", []):
                # Extract athlete ID from $ref URL
                ath_ref = leader.get("athlete", {}).get("$ref", "")
                ath_id = _extract_athlete_id(ath_ref)
                if not ath_id:
                    continue
                value = leader.get("value")
                if value is not None:
                    if ath_id not in player_stats:
                        player_stats[ath_id] = {}
                    try:
                        player_stats[ath_id][stat_key] = float(value)
                    except (ValueError, TypeError):
                        pass

        if not player_stats:
            return None

        # Build player list sorted by PPG + APG + RPG descending
        players: list[dict[str, Any]] = []
        for ath_id, stats in player_stats.items():
            roster_info = roster_map.get(ath_id, {})
            sort_value = stats.get("ppg", 0) + stats.get("apg", 0) + stats.get("rpg", 0)
            player_info: dict[str, Any] = {
                "id": ath_id,
                "name": roster_info.get("name", ""),
                "jersey": roster_info.get("jersey", ""),
                "position": roster_info.get("position", ""),
                "ppg": round(stats.get("ppg", 0), 1),
                "apg": round(stats.get("apg", 0), 1),
                "rpg": round(stats.get("rpg", 0), 1),
                "_sort": sort_value,
            }
            # Include other stats if available
            for key in ("spg", "bpg", "fg_pct", "mpg"):
                if key in stats:
                    player_info[key] = round(stats[key], 1)
            players.append(player_info)

        players.sort(key=lambda p: p.pop("_sort", 0), reverse=True)

        if not players:
            return None

        return TeamPlayerStats(
            team_id=team_id,
            team_name=team_name,
            players=players,
        )
    except Exception as e:
        logger.warning("Failed to parse player stats for %s: %s", team_id, e)
        return None


def _extract_athlete_id(ref_url: str) -> str:
    """Extract athlete ID from an ESPN ``$ref`` URL.

    E.g. ``".../athletes/4278073?lang=en"`` → ``"4278073"``.
    """
    if not ref_url:
        return ""
    # Strip query params
    path = ref_url.split("?")[0].rstrip("/")
    parts = path.split("/")
    # Find "athletes" segment and return the next part
    for i, part in enumerate(parts):
        if part == "athletes" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


def _parse_standings(
    standings_raw: dict[str, Any],
    team_id: str,
    team_name: str,  # noqa: ARG001
) -> TeamStandings | None:
    """Parse conference and division standings for a specific team."""
    try:
        data = standings_raw.get("standings", {})
        if not data:
            return None

        # ESPN standings: children[] -> each conference -> standings.entries[]
        for conf in data.get("children", []):
            conf_name = conf.get("name", conf.get("abbreviation", ""))

            # Check direct entries (flat standings)
            entries = conf.get("standings", {}).get("entries", [])
            result = _find_team_in_entries(entries, team_id, conf_name, "")
            if result:
                return result

            # Check divisions within conference
            for div in conf.get("children", []):
                div_name = div.get("name", div.get("abbreviation", ""))
                div_entries = div.get("standings", {}).get("entries", [])
                result = _find_team_in_entries(
                    div_entries, team_id, conf_name, div_name
                )
                if result:
                    return result

        return None
    except Exception as e:
        logger.warning("Failed to parse standings for %s: %s", team_id, e)
        return None


def _find_team_in_entries(
    entries: list[dict[str, Any]],
    team_id: str,
    conference: str,
    division: str,
) -> TeamStandings | None:
    """Find a team in standings entries and build TeamStandings."""
    for i, entry in enumerate(entries):
        team = entry.get("team", {})
        if str(team.get("id", "")) != str(team_id):
            continue

        # Parse stats array into useful fields
        stats_list = entry.get("stats", [])
        stats_map: dict[str, Any] = {}
        for s in stats_list:
            name = s.get("name", s.get("abbreviation", ""))
            if name:
                stats_map[name] = s.get("value", s.get("displayValue", ""))

        overall_record = str(stats_map.get("overall", stats_map.get("Overall", "")))
        conf_record = str(stats_map.get("vs. Conf.", stats_map.get("vsConf", "")))
        games_back_val = stats_map.get("gamesBehind", stats_map.get("GB", 0))
        try:
            games_back = float(games_back_val) if games_back_val else 0.0
        except (ValueError, TypeError):
            games_back = 0.0

        # ESPN returns entries sorted worst-first (ascending), so rank is
        # total_entries - index for 1-indexed rank.
        total = len(entries)
        rank = total - i

        return TeamStandings(
            team_id=str(team_id),
            team_name=team.get("displayName", team.get("name", "")),
            conference=conference,
            conference_rank=rank,
            division=division,
            division_rank=rank if division else 0,
            overall_record=overall_record,
            conference_record=conf_record,
            games_back=games_back,
        )
    return None


__all__ = [
    "fetch_pregame_stats",
]
