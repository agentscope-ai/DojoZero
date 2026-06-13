"""World Cup–specific utility functions."""

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any

import aiohttp

from dojozero.data._game_info import GameInfo
from dojozero.data.world_cup._constants import WORLD_CUP_KNOWN_LEAGUES

logger = logging.getLogger(__name__)


_ESPN_SOCCER_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary"
)


def parse_iso_datetime(time_str: str) -> datetime:
    """Parse ESPN ISO timestamps; accepts trailing 'Z'."""
    return datetime.fromisoformat(time_str.replace("Z", "+00:00"))


def validate_world_cup_league(league: str) -> str:
    """Validate a supported FIFA league code before using it in ESPN URLs."""
    if league in WORLD_CUP_KNOWN_LEAGUES:
        return league
    if re.fullmatch(r"fifa\.[a-z0-9_.-]+", league):
        logger.warning(
            "Unknown FIFA league code %r; passing through for ESPN compatibility",
            league,
        )
        return league
    raise ValueError(
        f"Unknown FIFA league code: {league!r}. "
        f"Expected one of: {sorted(WORLD_CUP_KNOWN_LEAGUES)} "
        "or a future ESPN FIFA code like 'fifa.worldq.intercontinental'."
    )


def id_from_ref(obj: dict[str, Any] | None) -> str:
    """Extract the trailing numeric ID from an ESPN ``$ref`` URL."""
    if not obj:
        return ""
    ref = obj.get("$ref", "")
    if not ref:
        return ""
    path = ref.split("?", 1)[0]
    segment = path.rsplit("/", 1)[-1]
    return segment if segment else ""


def _first_record_summary(records: list[dict[str, Any]]) -> str:
    """Pull the first record summary string out of ESPN competitor records."""
    for r in records:
        if isinstance(r, dict) and r.get("summary"):
            return str(r["summary"])
    return ""


def _team_dict_from_competitor(competitor: dict[str, Any]) -> dict[str, Any]:
    """Project an ESPN summary competitor into a dict TeamInfo can validate.

    Uses the alias keys ``TeamInfo`` expects (teamId, displayName, etc.).
    """
    team = competitor.get("team", {}) or {}
    logos = team.get("logos", []) or []
    logo_url = ""
    for logo in logos:
        if isinstance(logo, dict) and logo.get("href"):
            logo_url = logo["href"]
            break
    return {
        "teamId": str(team.get("id", "")),
        "displayName": team.get("displayName", "") or team.get("name", ""),
        "teamTricode": team.get("abbreviation", ""),
        "teamCity": team.get("location", ""),
        "shortDisplayName": team.get("shortDisplayName", ""),
        "color": team.get("color", ""),
        "alternateColor": team.get("alternateColor", ""),
        "logo": logo_url,
        "record": _first_record_summary(competitor.get("records", [])),
    }


def build_game_info_from_summary(
    summary: dict[str, Any],
    game_id: str,
) -> GameInfo | None:
    """Build a ``GameInfo`` from an ESPN soccer summary payload.

    Constructs an aliased dict and runs it through ``GameInfo.model_validate``
    so that Pydantic field aliases line up with ESPN's payload keys.
    """
    header = summary.get("header", {}) or {}
    competitions = header.get("competitions", []) or []
    if not competitions:
        return None
    comp = competitions[0]

    competitors = comp.get("competitors", []) or []
    home_competitor: dict[str, Any] = {}
    away_competitor: dict[str, Any] = {}
    for c in competitors:
        if not isinstance(c, dict):
            continue
        if c.get("homeAway") == "home":
            home_competitor = c
        elif c.get("homeAway") == "away":
            away_competitor = c
    if not home_competitor or not away_competitor:
        return None

    venue_dict = (summary.get("gameInfo", {}) or {}).get("venue", {}) or {}
    addr = venue_dict.get("address", {}) or {}
    venue_data = {
        "venueId": str(venue_dict.get("id", "")),
        "name": venue_dict.get("fullName", "") or venue_dict.get("shortName", ""),
        "city": addr.get("city", ""),
        "state": addr.get("state", "") or addr.get("country", ""),
        "indoor": bool(venue_dict.get("indoor", False)),
    }

    season = header.get("season", {}) or {}
    raw_season_type = season.get("type", "")
    season_type = (
        "regular"
        if isinstance(raw_season_type, int) and raw_season_type == 2
        else str(raw_season_type or "")
    )

    broadcast = ""
    broadcasts_raw = comp.get("broadcasts", []) or []
    for b in broadcasts_raw:
        if isinstance(b, dict):
            names = b.get("names") or []
            if names:
                broadcast = ", ".join(str(n) for n in names if n)
                break

    game_data: dict[str, Any] = {
        "gameId": game_id,
        "sport_type": "world_cup",
        "homeTeam": _team_dict_from_competitor(home_competitor),
        "awayTeam": _team_dict_from_competitor(away_competitor),
        "venue": venue_data,
        "gameTimeUTC": comp.get("date") or None,
        "broadcasts": broadcasts_raw,
        "broadcast": broadcast,
        "neutralSite": bool(comp.get("neutralSite", False)),
        "seasonYear": int(season.get("year", 0) or 0),
        "seasonType": season_type,
    }

    return GameInfo.model_validate(game_data)


async def get_game_info_by_id_async(
    game_id: str,
    league: str = "fifa.world",
    proxy: str | None = None,
    timeout: int = 30,
) -> GameInfo | None:
    """Fetch the ESPN soccer summary for ``game_id`` and return a ``GameInfo``.

    Args:
        game_id: ESPN event ID.
        league: FIFA league code (default ``fifa.world``).
        proxy: Optional proxy URL; falls back to ``DOJOZERO_PROXY_URL``.
        timeout: HTTP timeout seconds.

    Returns:
        ``GameInfo`` or ``None`` if the summary is missing or malformed.
    """
    if not game_id:
        return None
    league = validate_world_cup_league(league)
    proxy = proxy if proxy is not None else os.getenv("DOJOZERO_PROXY_URL")
    url = _ESPN_SOCCER_SUMMARY_URL.format(league=league)
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.get(url, params={"event": game_id}, proxy=proxy) as resp:
                if resp.status != 200:
                    logger.warning(
                        "World Cup summary fetch returned %d for event %s",
                        resp.status,
                        game_id,
                    )
                    return None
                summary = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("World Cup summary fetch failed for event %s: %s", game_id, exc)
        return None

    return build_game_info_from_summary(summary, game_id)


__all__ = [
    "build_game_info_from_summary",
    "get_game_info_by_id_async",
    "id_from_ref",
    "parse_iso_datetime",
    "validate_world_cup_league",
]
