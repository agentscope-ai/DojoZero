"""NBA-specific utility functions."""

import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

from dojozero.utils import utc_to_us_date

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from sync context, handling existing event loops.

    This handles the case where we're called from within an async context
    (like FastAPI/uvicorn) where an event loop already exists.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop, we can create one
        loop = None

    if loop is not None:
        # We're in an async context, need to run in a new thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        # No running loop, safe to use asyncio.run
        return asyncio.run(coro)


def parse_iso_datetime(time_str: str) -> datetime:
    """Parse ISO format datetime string, handling 'Z' suffix.

    NBA API returns timestamps with 'Z' suffix (e.g., '2025-01-07T02:00:00Z')
    which Python's fromisoformat() doesn't handle directly until Python 3.11.

    Args:
        time_str: ISO format datetime string (e.g., '2025-01-07T02:00:00Z')

    Returns:
        datetime object with UTC timezone

    Examples:
        >>> parse_iso_datetime('2025-01-07T02:00:00Z')
        datetime(2025, 1, 7, 2, 0, 0, tzinfo=timezone.utc)
    """
    return datetime.fromisoformat(time_str.replace("Z", "+00:00"))


def get_proxy() -> str | None:
    """Get proxy configuration from environment variables.

    Returns:
        Proxy URL string, or None if not configured
    """
    return os.getenv("DOJOZERO_PROXY_URL")


# ESPN API base URL
ESPN_SITE_API_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"


# Common NBA team name variations for matching
# Keys are team tricodes (standardized NBA team identifiers)
# First element in each list is the full official team name
TEAM_NAME_VARIATIONS: dict[str, list[str]] = {
    "MIA": ["Miami Heat", "heat", "miami heat", "miami"],
    "ORL": ["Orlando Magic", "magic", "orlando magic", "orlando"],
    "LAL": ["Los Angeles Lakers", "lakers", "los angeles lakers", "la lakers"],
    "BOS": ["Boston Celtics", "celtics", "boston celtics", "boston"],
    "GSW": [
        "Golden State Warriors",
        "warriors",
        "golden state warriors",
        "golden state",
    ],
    "PHX": ["Phoenix Suns", "suns", "phoenix suns", "phoenix"],
    "OKC": [
        "Oklahoma City Thunder",
        "thunder",
        "oklahoma city thunder",
        "oklahoma city",
    ],
    "DET": ["Detroit Pistons", "pistons", "detroit pistons", "detroit"],
    "TOR": ["Toronto Raptors", "raptors", "toronto raptors", "toronto"],
    "CHI": ["Chicago Bulls", "bulls", "chicago bulls", "chicago"],
    "NYK": ["New York Knicks", "knicks", "new york knicks", "ny knicks"],
    "BKN": ["Brooklyn Nets", "nets", "brooklyn nets", "brooklyn"],
    "PHI": [
        "Philadelphia 76ers",
        "76ers",
        "sixers",
        "philadelphia 76ers",
        "philadelphia",
    ],
    "ATL": ["Atlanta Hawks", "hawks", "atlanta hawks", "atlanta"],
    "CHA": ["Charlotte Hornets", "hornets", "charlotte hornets", "charlotte"],
    "CLE": [
        "Cleveland Cavaliers",
        "cavaliers",
        "cavs",
        "cleveland cavaliers",
        "cleveland",
    ],
    "DAL": ["Dallas Mavericks", "mavericks", "mavs", "dallas mavericks", "dallas"],
    "DEN": ["Denver Nuggets", "nuggets", "denver nuggets", "denver"],
    "HOU": ["Houston Rockets", "rockets", "houston rockets", "houston"],
    "IND": ["Indiana Pacers", "pacers", "indiana pacers", "indiana"],
    "LAC": ["Los Angeles Clippers", "clippers", "la clippers", "los angeles clippers"],
    "MEM": ["Memphis Grizzlies", "grizzlies", "memphis grizzlies", "memphis"],
    "MIN": [
        "Minnesota Timberwolves",
        "timberwolves",
        "wolves",
        "minnesota timberwolves",
        "minnesota",
    ],
    "NOP": ["New Orleans Pelicans", "pelicans", "new orleans pelicans", "new orleans"],
    "POR": [
        "Portland Trail Blazers",
        "trail blazers",
        "blazers",
        "portland trail blazers",
        "portland",
    ],
    "SAC": ["Sacramento Kings", "kings", "sacramento kings", "sacramento"],
    "SAS": ["San Antonio Spurs", "spurs", "san antonio spurs", "san antonio"],
    "UTA": ["Utah Jazz", "jazz", "utah jazz", "utah"],
    "WAS": ["Washington Wizards", "wizards", "washington wizards", "washington"],
}


def extract_team_names_from_query(query: str) -> set[str]:
    """Extract team tricodes from query string.

    Args:
        query: Search query string

    Returns:
        Set of team tricodes found in query (e.g., {"LAL", "SAS"})
    """
    query_lower = query.lower()
    found_teams = set()

    # Check each team's variations
    for tricode, variations in TEAM_NAME_VARIATIONS.items():
        for variation in variations:
            if variation in query_lower:
                found_teams.add(tricode)
                break

    return found_teams


def normalize_team_name(team_name: str) -> str | None:
    """Normalize a team name to team tricode.

    Args:
        team_name: Team name from ranking data (can be full name, city, tricode, etc.)

    Returns:
        Team tricode (e.g., "LAL", "SAS") or None if not found
    """
    team_lower = team_name.lower()

    # Direct tricode match (case-insensitive)
    team_upper = team_name.upper()
    if team_upper in TEAM_NAME_VARIATIONS:
        return team_upper

    # Check variations
    for tricode, variations in TEAM_NAME_VARIATIONS.items():
        if team_lower in variations:
            return tricode
        # Also check if team name contains any variation
        for variation in variations:
            if variation in team_lower or team_lower in variation:
                return tricode

    return None


async def _fetch_espn_scoreboard(
    game_date: date,
    timeout: int = 30,
    proxy: str | None = None,
) -> dict[str, Any]:
    """Fetch scoreboard data from ESPN API.

    Args:
        game_date: Date to fetch games for
        timeout: Request timeout in seconds
        proxy: Optional proxy URL

    Returns:
        ESPN scoreboard response dict
    """
    date_str = game_date.strftime("%Y%m%d")
    url = f"{ESPN_SITE_API_BASE}/scoreboard"
    params = {"dates": date_str}

    proxy = proxy if proxy is not None else get_proxy()

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout)
    ) as session:
        try:
            async with session.get(url, params=params, proxy=proxy) as response:
                if response.status != 200:
                    logger.warning(
                        "ESPN scoreboard request failed: status=%d, date=%s",
                        response.status,
                        date_str,
                    )
                    return {"events": []}
                return await response.json()
        except Exception as e:
            logger.error("Error fetching ESPN scoreboard for date %s: %s", date_str, e)
            return {"events": []}


def _parse_espn_event(event: dict[str, Any], game_date: date) -> dict[str, Any]:
    """Parse an ESPN event into our standard game format.

    Captures all game data from ESPN to avoid additional API calls in the UI.

    Args:
        event: ESPN event dict
        game_date: The date of the game

    Returns:
        Game dict in our standard format with all ESPN data
    """
    event_id = event.get("id", "")
    event_date = event.get("date", "")

    # Parse game time
    game_time_ltz = None
    if event_date:
        try:
            from datetime import timezone

            from dateutil import parser

            game_time_utc = parser.parse(event_date)
            game_time_ltz = game_time_utc.replace(tzinfo=timezone.utc).astimezone(
                tz=None
            )
        except Exception:
            pass

    # Get competition data
    competitions = event.get("competitions", [])
    if not competitions:
        return {}

    comp = competitions[0]
    competitors = comp.get("competitors", [])

    home_team: dict[str, Any] = {}
    away_team: dict[str, Any] = {}

    for competitor in competitors:
        team_info = competitor.get("team", {})
        is_home = competitor.get("homeAway") == "home"
        score = competitor.get("score", "0")
        record = competitor.get("records", [{}])[0] if competitor.get("records") else {}

        # Parse wins/losses from record
        wins = 0
        losses = 0
        if record and record.get("summary"):
            try:
                parts = record["summary"].split("-")
                if len(parts) == 2:
                    wins = int(parts[0])
                    losses = int(parts[1])
            except (ValueError, IndexError):
                pass

        team_data = {
            "teamId": team_info.get("id", 0),
            "teamName": team_info.get("name", ""),
            "teamCity": team_info.get("location", ""),
            "teamTricode": team_info.get("abbreviation", ""),
            "displayName": team_info.get("displayName", ""),
            "shortDisplayName": team_info.get("shortDisplayName", ""),
            "color": team_info.get("color", ""),
            "alternateColor": team_info.get("alternateColor", ""),
            "logo": team_info.get("logo", ""),
            "score": int(score) if score else 0,
            "wins": wins,
            "losses": losses,
            "record": record.get("summary", "") if record else "",
        }

        if is_home:
            home_team = team_data
        else:
            away_team = team_data

    # Get game status
    status = comp.get("status", {})
    status_type = status.get("type", {})
    status_id = int(status_type.get("id", 0))
    status_text = status_type.get("shortDetail", "")

    # Map ESPN status to our format: 1=scheduled, 2=in-progress, 3=finished
    # ESPN: 1=scheduled, 2=in-progress, 3=final
    game_status = status_id

    # Get period info
    period = status.get("period", 0)
    clock = status.get("displayClock", "")

    # Get venue info
    venue_data = comp.get("venue", {})
    venue_address = venue_data.get("address", {})
    venue = {
        "venueId": str(venue_data.get("id", "")),
        "name": venue_data.get("fullName", ""),
        "city": venue_address.get("city", ""),
        "state": venue_address.get("state", ""),
        "indoor": venue_data.get("indoor", True),
    }

    # Get broadcast info - capture all broadcasts
    broadcasts_raw = comp.get("broadcasts", [])
    broadcasts: list[dict[str, Any]] = []
    broadcast_names: list[str] = []
    for b in broadcasts_raw:
        market = b.get("market", "")
        names = b.get("names", [])
        broadcasts.append({"market": market, "names": names})
        if names:
            broadcast_names.extend(names)
    broadcast = ", ".join(broadcast_names) if broadcast_names else ""

    # Get odds
    odds_list = comp.get("odds", [])
    odds: dict[str, Any] = {}
    if odds_list:
        o = odds_list[0]
        odds = {
            "provider": o.get("provider", {}).get("name", ""),
            "spread": o.get("spread", 0),
            "overUnder": o.get("overUnder", 0),
            "homeMoneyLine": o.get("homeTeamOdds", {}).get("moneyLine", 0),
            "awayMoneyLine": o.get("awayTeamOdds", {}).get("moneyLine", 0),
        }

    # Get game state
    attendance = comp.get("attendance", 0)
    neutral_site = comp.get("neutralSite", False)

    # Get season info from event
    season = event.get("season", {})
    season_year = season.get("year", 0)
    season_type_id = season.get("type", 0)
    season_type_map = {1: "preseason", 2: "regular", 3: "postseason", 4: "offseason"}
    season_type = season_type_map.get(season_type_id, "")

    # Get game names from event
    game_name = event.get("name", "")
    short_name = event.get("shortName", "")

    return {
        "gameId": event_id,
        "gameStatus": game_status,
        "gameStatusText": status_text,
        "period": period,
        "gameClock": clock,
        "gameTimeUTC": event_date,
        "gameTimeLTZ": game_time_ltz,
        "homeTeam": home_team,
        "awayTeam": away_team,
        "gameLeaders": {},  # ESPN doesn't provide this in scoreboard
        # Additional ESPN fields
        "venue": venue,
        "broadcasts": broadcasts,
        "broadcast": broadcast,
        "odds": odds,
        "attendance": attendance,
        "neutralSite": neutral_site,
        "seasonYear": season_year,
        "seasonType": season_type,
        "name": game_name,
        "shortName": short_name,
    }


def get_games_for_date(
    game_date: datetime | str,
    print_games: bool = False,
    proxy: str | None = None,
) -> list[dict[str, Any]]:
    """Get games for a specific date using ESPN API.

    Args:
        game_date: Date as datetime object or string in 'YYYY-MM-DD' format
        print_games: Whether to print game information (default: False)
        proxy: Optional proxy URL (automatically set from DOJOZERO_PROXY_URL env var)

    Returns:
        list[dict]: List of game dictionaries with the following structure:
        {
            'gameId': str,
            'gameStatus': int,  # 1=scheduled, 2=in-progress, 3=finished
            'gameStatusText': str,
            'period': int,
            'gameClock': str,
            'gameTimeUTC': str,
            'gameTimeLTZ': datetime | None,  # Local timezone
            'homeTeam': {
                'teamId': int,
                'teamName': str,
                'teamCity': str,
                'teamTricode': str,
                'score': int,
                'wins': int,
                'losses': int
            },
            'awayTeam': {...},  # Same structure as homeTeam
            'gameLeaders': dict  # May be empty when game hasn't started
        }
        Returns empty list if no games found or on error.
    """
    try:
        from dateutil import parser

        # Parse the requested date
        if isinstance(game_date, datetime):
            requested_date = game_date.date()
        elif isinstance(game_date, str):
            try:
                parsed_date = parser.parse(game_date).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                requested_date = parsed_date.date()
            except Exception:
                requested_date = None
        else:
            requested_date = None

        if not requested_date:
            if print_games:
                print(f"Error: Could not parse date: {game_date}")
            return []

        # Run async fetch in sync context
        scoreboard_data = _run_async(
            _fetch_espn_scoreboard(requested_date, proxy=proxy)
        )

        events = scoreboard_data.get("events", [])
        games = []

        for event in events:
            game = _parse_espn_event(event, requested_date)
            if game:
                games.append(game)

        if print_games:
            date_str = requested_date.strftime("%Y-%m-%d")
            print(f"Date: {date_str}")
            print(f"Found {len(games)} game(s)\n")
            for game in games:
                time_str = (
                    game["gameTimeLTZ"].strftime("%Y-%m-%d %H:%M:%S %Z")
                    if game.get("gameTimeLTZ")
                    else "N/A"
                )
                print(
                    f"{game['gameId']}: {game['awayTeam']['teamName']} vs. {game['homeTeam']['teamName']} @ {time_str} [{game['gameStatusText']}]"
                )

        return games

    except Exception as e:
        logger.error("Error fetching games for date %s: %s", game_date, e)
        if print_games:
            print(f"Error fetching games for date {game_date}: {e}")
        return []


async def get_games_for_date_async(
    game_date: datetime | str,
    proxy: str | None = None,
) -> list[dict[str, Any]]:
    """Async version of get_games_for_date using ESPN API.

    Args:
        game_date: Date as datetime object or string in 'YYYY-MM-DD' format
        proxy: Optional proxy URL

    Returns:
        List of game dictionaries
    """
    try:
        from dateutil import parser

        # Parse the requested date
        if isinstance(game_date, datetime):
            requested_date = game_date.date()
        elif isinstance(game_date, str):
            try:
                parsed_date = parser.parse(game_date).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                requested_date = parsed_date.date()
            except Exception:
                return []
        else:
            return []

        scoreboard_data = await _fetch_espn_scoreboard(requested_date, proxy=proxy)
        events = scoreboard_data.get("events", [])

        games = []
        for event in events:
            game = _parse_espn_event(event, requested_date)
            if game:
                games.append(game)

        return games

    except Exception as e:
        logger.error("Error fetching games for date %s: %s", game_date, e)
        return []


def get_games_by_date_range(
    start_date: date,
    end_date: date,
    proxy: str | None = None,
) -> list[dict[str, Any]]:
    """Get all games within a date range using ESPN API.

    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        proxy: Optional proxy URL

    Returns:
        List of game dictionaries with structure:
        {
            'game_id': str,
            'home_team': str,
            'away_team': str,
            'home_team_tricode': str,
            'away_team_tricode': str,
            'game_date': str,
            'game_time_utc': str,
            'game_status': int,  # 1=scheduled, 2=in-progress, 3=completed
        }
    """
    games = []
    current_date = start_date

    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        logger.debug(f"Fetching games for date={date_str}")

        try:
            # Run async fetch in sync context
            scoreboard_data = _run_async(
                _fetch_espn_scoreboard(current_date, proxy=proxy)
            )

            events = scoreboard_data.get("events", [])

            for event in events:
                game = _parse_espn_event(event, current_date)
                if game:
                    home_team = game.get("homeTeam", {})
                    away_team = game.get("awayTeam", {})

                    home_team_name = f"{home_team.get('teamCity', '')} {home_team.get('teamName', '')}".strip()
                    away_team_name = f"{away_team.get('teamCity', '')} {away_team.get('teamName', '')}".strip()

                    games.append(
                        {
                            "game_id": str(game.get("gameId", "")),
                            "home_team": home_team_name,
                            "away_team": away_team_name,
                            "home_team_tricode": home_team.get("teamTricode", ""),
                            "away_team_tricode": away_team.get("teamTricode", ""),
                            "game_date": date_str,
                            "game_time_utc": game.get("gameTimeUTC", ""),
                            "game_status": game.get("gameStatus", 0),
                        }
                    )

            logger.debug(f"Found {len(events)} games on {date_str}")

        except Exception as e:
            logger.debug(f"Error fetching games for date={date_str}: {e}")

        current_date += timedelta(days=1)

    logger.debug(f"Found {len(games)} total games between {start_date} and {end_date}")
    return games


async def get_games_by_date_range_async(
    start_date: date,
    end_date: date,
    proxy: str | None = None,
) -> list[dict[str, Any]]:
    """Async version of get_games_by_date_range.

    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        proxy: Optional proxy URL

    Returns:
        List of game dictionaries
    """
    games = []
    current_date = start_date

    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        logger.debug(f"Fetching games for date={date_str}")

        try:
            scoreboard_data = await _fetch_espn_scoreboard(current_date, proxy=proxy)
            events = scoreboard_data.get("events", [])

            for event in events:
                game = _parse_espn_event(event, current_date)
                if game:
                    home_team = game.get("homeTeam", {})
                    away_team = game.get("awayTeam", {})

                    home_team_name = f"{home_team.get('teamCity', '')} {home_team.get('teamName', '')}".strip()
                    away_team_name = f"{away_team.get('teamCity', '')} {away_team.get('teamName', '')}".strip()

                    games.append(
                        {
                            "game_id": str(game.get("gameId", "")),
                            "home_team": home_team_name,
                            "away_team": away_team_name,
                            "home_team_tricode": home_team.get("teamTricode", ""),
                            "away_team_tricode": away_team.get("teamTricode", ""),
                            "game_date": date_str,
                            "game_time_utc": game.get("gameTimeUTC", ""),
                            "game_status": game.get("gameStatus", 0),
                        }
                    )

            logger.debug(f"Found {len(events)} games on {date_str}")

        except Exception as e:
            logger.debug(f"Error fetching games for date={date_str}: {e}")

        current_date += timedelta(days=1)

    logger.debug(f"Found {len(games)} total games between {start_date} and {end_date}")
    return games


async def _fetch_espn_summary(
    event_id: str,
    timeout: int = 30,
    proxy: str | None = None,
) -> dict[str, Any]:
    """Fetch game summary from ESPN API.

    Args:
        event_id: ESPN event ID
        timeout: Request timeout in seconds
        proxy: Optional proxy URL

    Returns:
        ESPN summary response dict
    """
    url = f"{ESPN_SITE_API_BASE}/summary"
    params = {"event": event_id}

    proxy = proxy if proxy is not None else get_proxy()

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout)
    ) as session:
        try:
            async with session.get(url, params=params, proxy=proxy) as response:
                if response.status != 200:
                    logger.warning(
                        "ESPN summary request failed: status=%d, event_id=%s",
                        response.status,
                        event_id,
                    )
                    return {}
                return await response.json()
        except Exception as e:
            logger.error("Error fetching ESPN summary for event %s: %s", event_id, e)
            return {}


def _is_nba_game_id(game_id: str) -> bool:
    """Check if the game_id is an NBA.com game ID format.

    NBA game IDs are 10 digits starting with 00, 01, 02, etc.
    ESPN event IDs are typically 9 digits starting with 4.
    """
    return len(game_id) == 10 and game_id.startswith("00")


def _extract_game_info_from_summary(
    summary: dict[str, Any], game_id: str
) -> dict[str, Any] | None:
    """Extract game info from ESPN summary response."""
    header = summary.get("header", {})
    competitions = header.get("competitions", [])

    if not competitions:
        return None

    comp = competitions[0]
    competitors = comp.get("competitors", [])

    home_team_name = ""
    away_team_name = ""
    home_team_tricode = ""
    away_team_tricode = ""

    for competitor in competitors:
        team_info = competitor.get("team", {})
        team_name = (
            f"{team_info.get('location', '')} {team_info.get('name', '')}".strip()
        )
        team_tricode = team_info.get("abbreviation", "")

        if competitor.get("homeAway") == "home":
            home_team_name = team_name
            home_team_tricode = team_tricode
        else:
            away_team_name = team_name
            away_team_tricode = team_tricode

    # Get game date/time
    # Convert to US Eastern time for the date since NBA games are scheduled in local time
    # and Polymarket slugs use the US date, not UTC date
    game_time_utc = comp.get("date", "")
    game_date = ""
    if game_time_utc:
        try:
            from dateutil import parser

            dt = parser.parse(game_time_utc)
            game_date = utc_to_us_date(dt)
        except Exception:
            pass

    if not home_team_name or not away_team_name:
        return None

    return {
        "game_id": game_id,
        "home_team": home_team_name,
        "away_team": away_team_name,
        "home_team_tricode": home_team_tricode,
        "away_team_tricode": away_team_tricode,
        "game_date": game_date,
        "game_time_utc": game_time_utc,
    }


def get_game_info_by_id(
    game_id: str, proxy: str | None = None
) -> dict[str, Any] | None:
    """Get team names and game date for a given game/event ID using ESPN API.

    Supports both ESPN event IDs (e.g., '401584701') and legacy NBA.com game IDs
    (e.g., '0022500640'). For NBA.com IDs, this function will search recent dates
    to find a matching game.

    Args:
        game_id: ESPN event ID or NBA.com game ID
        proxy: Optional proxy URL

    Returns:
        Dictionary with game information:
        {
            'game_id': str,
            'home_team': str,  # Full team name (e.g., "Los Angeles Lakers")
            'away_team': str,  # Full team name (e.g., "San Antonio Spurs")
            'home_team_tricode': str,  # Team tricode (e.g., "LAL")
            'away_team_tricode': str,  # Team tricode (e.g., "SAS")
            'game_date': str,  # Date in YYYY-MM-DD format
            'game_time_utc': str,  # Game time in UTC (ISO format)
        }
        Returns None if game not found or is invalid
    """
    logger.debug(f"Looking up game_id={game_id}")

    # First, try direct ESPN lookup (works for ESPN event IDs)
    try:
        summary = _run_async(_fetch_espn_summary(game_id, proxy=proxy))

        if summary:
            result = _extract_game_info_from_summary(summary, game_id)
            if result:
                logger.debug(
                    f"Found game via ESPN: {result['away_team_tricode']} @ {result['home_team_tricode']}"
                )
                return result
    except Exception as e:
        logger.debug(f"ESPN direct lookup failed for game_id={game_id}: {e}")

    # If direct lookup failed and this looks like an NBA.com game ID,
    # we can't look it up via ESPN (different ID systems)
    if _is_nba_game_id(game_id):
        logger.warning(
            f"game_id={game_id} appears to be an NBA.com game ID. "
            "ESPN uses different event IDs. Please use ESPN event IDs for new trials. "
            "You can find ESPN event IDs from the scoreboard API or game URLs."
        )
        return None

    logger.debug(f"No game info found for game_id={game_id}")
    return None


async def get_game_info_by_id_async(
    game_id: str, proxy: str | None = None
) -> dict[str, Any] | None:
    """Async version of get_game_info_by_id using ESPN API.

    Supports both ESPN event IDs (e.g., '401584701') and legacy NBA.com game IDs
    (e.g., '0022500640'). For NBA.com IDs, this function will log a warning
    as ESPN uses different event IDs.

    Args:
        game_id: ESPN event ID or NBA.com game ID
        proxy: Optional proxy URL

    Returns:
        Dictionary with game information or None if not found
    """
    logger.debug(f"Looking up game_id={game_id}")

    # First, try direct ESPN lookup
    try:
        summary = await _fetch_espn_summary(game_id, proxy=proxy)

        if summary:
            result = _extract_game_info_from_summary(summary, game_id)
            if result:
                logger.debug(
                    f"Found game via ESPN: {result['away_team_tricode']} @ {result['home_team_tricode']}"
                )
                return result
    except Exception as e:
        logger.debug(f"ESPN direct lookup failed for game_id={game_id}: {e}")

    # If direct lookup failed and this looks like an NBA.com game ID,
    # we can't look it up via ESPN (different ID systems)
    if _is_nba_game_id(game_id):
        logger.warning(
            f"game_id={game_id} appears to be an NBA.com game ID. "
            "ESPN uses different event IDs. Please use ESPN event IDs for new trials. "
            "You can find ESPN event IDs from the scoreboard API or game URLs."
        )
        return None

    logger.debug(f"No game info found for game_id={game_id}")
    return None
