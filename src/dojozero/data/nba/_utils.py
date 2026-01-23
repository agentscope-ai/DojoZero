"""NBA-specific utility functions."""

import json
import os
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any, Callable, TypeVar, cast

import requests


F = TypeVar("F", bound=Callable[..., Any])


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


def with_proxy(func: F) -> F:
    """Decorator to ensure DOJOZERO_PROXY_URL is set up for NBA API calls.

    This decorator:
    1. Checks if DOJOZERO_PROXY_URL is available
    2. Passes proxy parameter to functions that accept it (checks function signature)
    3. Handles ImportError if nba_api is not available

    Usage:
        @with_proxy
        def my_nba_function(game_id: str):
            from dojozero.data.nba._utils import get_proxy
            proxy = get_proxy()
            from nba_api.stats.endpoints import scoreboardv3
            board = scoreboardv3.ScoreboardV3(game_date=date_str, proxy=proxy) if proxy else scoreboardv3.ScoreboardV3(game_date=date_str)
            ...
    """
    import inspect

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Check if function accepts 'proxy' parameter
        sig = inspect.signature(func)
        if "proxy" in sig.parameters and "proxy" not in kwargs:
            kwargs["proxy"] = get_proxy()

        try:
            return func(*args, **kwargs)
        except ImportError as e:
            # nba_api not available
            raise ImportError(
                f"nba_api library is required for {func.__name__}. "
                "Install it with: pip install nba-api"
            ) from e

    # Cast is needed here because pyright cannot infer that @wraps preserves the function type
    return cast(F, wrapper)


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


@with_proxy
def get_games_for_date(
    game_date: datetime | str,
    print_games: bool = False,
    proxy: str | None = None,
) -> list[dict[str, Any]]:
    """Get games for a specific date using ScoreboardV3 endpoint.

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
    import logging

    from dateutil import parser
    from nba_api.stats.endpoints import scoreboardv3

    logger = logging.getLogger(__name__)

    try:
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

        date_str = requested_date.strftime("%Y-%m-%d")

        board = (
            scoreboardv3.ScoreboardV3(game_date=date_str, proxy=proxy)
            if proxy
            else scoreboardv3.ScoreboardV3(game_date=date_str)
        )
        games_data = board.get_dict()

        if not games_data or "scoreboard" not in games_data:
            return []

        scoreboard_data = games_data["scoreboard"]
        games_list = scoreboard_data.get("games", [])

        # Convert ScoreboardV3 format to our standard format
        games = []
        for game_data in games_list:
            game: dict[str, Any] = {
                "gameId": game_data.get("gameId", ""),
                "gameStatus": game_data.get("gameStatus", 0),
                "gameStatusText": game_data.get("gameStatusText", "Unknown"),
                "period": game_data.get("period", 0),
                "gameClock": game_data.get("gameClock", ""),
                "gameTimeUTC": game_data.get("gameTimeUTC", ""),
                "homeTeam": {
                    "teamId": game_data.get("homeTeam", {}).get("teamId", 0),
                    "teamName": game_data.get("homeTeam", {}).get("teamName", ""),
                    "teamCity": game_data.get("homeTeam", {}).get("teamCity", ""),
                    "teamTricode": game_data.get("homeTeam", {}).get("teamTricode", ""),
                    "score": game_data.get("homeTeam", {}).get("score", 0),
                    "wins": game_data.get("homeTeam", {}).get("wins", 0),
                    "losses": game_data.get("homeTeam", {}).get("losses", 0),
                },
                "awayTeam": {
                    "teamId": game_data.get("awayTeam", {}).get("teamId", 0),
                    "teamName": game_data.get("awayTeam", {}).get("teamName", ""),
                    "teamCity": game_data.get("awayTeam", {}).get("teamCity", ""),
                    "teamTricode": game_data.get("awayTeam", {}).get("teamTricode", ""),
                    "score": game_data.get("awayTeam", {}).get("score", 0),
                    "wins": game_data.get("awayTeam", {}).get("wins", 0),
                    "losses": game_data.get("awayTeam", {}).get("losses", 0),
                },
                "gameLeaders": game_data.get("gameLeaders", {}),
            }

            # Parse game time
            if game.get("gameTimeUTC"):
                try:
                    from datetime import timezone

                    game_time_utc = parser.parse(game["gameTimeUTC"])
                    game_time_ltz = game_time_utc.replace(
                        tzinfo=timezone.utc
                    ).astimezone(tz=None)
                    game["gameTimeLTZ"] = game_time_ltz
                except Exception:
                    game["gameTimeLTZ"] = None

            games.append(game)

        if print_games:
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


@with_proxy
def get_games_by_date_range(
    start_date: date,
    end_date: date,
    proxy: str | None = None,
) -> list[dict[str, Any]]:
    """Get all games within a date range.

    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        proxy: Optional proxy URL (automatically set from DOJOZERO_PROXY_URL env var if not provided)

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
    import logging

    from nba_api.stats.endpoints import scoreboardv3

    logger = logging.getLogger(__name__)

    games = []
    current_date = start_date

    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        logger.debug(f"Fetching games for date={date_str}")

        try:
            if proxy:
                board = scoreboardv3.ScoreboardV3(game_date=date_str, proxy=proxy)
            else:
                board = scoreboardv3.ScoreboardV3(game_date=date_str)

            games_data = board.get_dict()

            if games_data and "scoreboard" in games_data:
                scoreboard_data = games_data["scoreboard"]
                games_list = scoreboard_data.get("games", [])

                for game_data in games_list:
                    home_team = game_data.get("homeTeam", {})
                    away_team = game_data.get("awayTeam", {})

                    home_team_name = f"{home_team.get('teamCity', '')} {home_team.get('teamName', '')}".strip()
                    away_team_name = f"{away_team.get('teamCity', '')} {away_team.get('teamName', '')}".strip()

                    games.append(
                        {
                            "game_id": str(game_data.get("gameId", "")),
                            "home_team": home_team_name,
                            "away_team": away_team_name,
                            "home_team_tricode": home_team.get("teamTricode", ""),
                            "away_team_tricode": away_team.get("teamTricode", ""),
                            "game_date": date_str,
                            "game_time_utc": game_data.get("gameTimeUTC", ""),
                            "game_status": game_data.get("gameStatus", 0),
                        }
                    )

                logger.debug(f"Found {len(games_list)} games on {date_str}")
        except requests.exceptions.RequestException as e:
            logger.debug(f"Network error fetching games for date={date_str}: {e}")
        except json.JSONDecodeError as e:
            logger.debug(f"JSON parsing error fetching games for date={date_str}: {e}")
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Data parsing error fetching games for date={date_str}: {e}")

        current_date += timedelta(days=1)

    logger.debug(f"Found {len(games)} total games between {start_date} and {end_date}")
    return games


@with_proxy
def get_game_info_by_id(
    game_id: str, proxy: str | None = None
) -> dict[str, Any] | None:
    """Get team names and game date for a given NBA game ID.

    This function uses a hybrid approach:
    1. First tries BoxScoreTraditionalV3 (fast, works for past/completed games)
    2. If that fails, searches upcoming games in ScoreboardV3 (for future games)

    Args:
        game_id: NBA.com game ID (e.g., '0022500290')
        proxy: Optional proxy URL (automatically set from DOJOZERO_PROXY_URL env var if not provided)

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
    import logging

    from nba_api.stats.endpoints import boxscoretraditionalv3, scoreboardv3

    logger = logging.getLogger(__name__)

    logger.debug(f"Looking up game_id={game_id}")

    # Strategy 1: Try BoxScore endpoint (works for completed/in-progress games)
    logger.debug(f"Attempting BoxScore lookup for game_id={game_id}")
    try:
        if proxy:
            box_score = boxscoretraditionalv3.BoxScoreTraditionalV3(
                game_id=game_id, proxy=proxy
            )
        else:
            box_score = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id)

        box_score_dict = box_score.get_dict()

        if box_score_dict and "boxScoreTraditional" in box_score_dict:
            boxscore_data = box_score_dict["boxScoreTraditional"]

            if boxscore_data and isinstance(boxscore_data, dict):
                # Extract team information from boxscore
                home_team = boxscore_data.get("homeTeam", {})
                away_team = boxscore_data.get("awayTeam", {})

                # Build full team name
                home_team_name = f"{home_team.get('teamCity', '')} {home_team.get('teamName', '')}".strip()
                away_team_name = f"{away_team.get('teamCity', '')} {away_team.get('teamName', '')}".strip()

                # If team names are available, we found the game
                if home_team_name and away_team_name:
                    logger.debug(
                        f"Found game via BoxScore: {away_team.get('teamTricode')} @ {home_team.get('teamTricode')}"
                    )

                    # BoxScore doesn't include date, use LeagueGameFinder to get it
                    game_date = ""
                    home_team_id = home_team.get("teamId")
                    if home_team_id:
                        try:
                            from nba_api.stats.endpoints import leaguegamefinder

                            # Get games for the home team to find the date
                            if proxy:
                                gamefinder = leaguegamefinder.LeagueGameFinder(
                                    team_id_nullable=home_team_id, proxy=proxy
                                )
                            else:
                                gamefinder = leaguegamefinder.LeagueGameFinder(
                                    team_id_nullable=home_team_id
                                )
                            games_df = gamefinder.get_data_frames()[0]
                            matching_game = games_df[games_df["GAME_ID"] == game_id]
                            if not matching_game.empty:
                                game_date = matching_game.iloc[0]["GAME_DATE"]
                                logger.debug(
                                    f"Found game date via LeagueGameFinder: {game_date}"
                                )
                        except requests.exceptions.RequestException as e:
                            logger.debug(f"LeagueGameFinder network error: {e}")
                        except (KeyError, IndexError, TypeError, ValueError) as e:
                            logger.debug(f"LeagueGameFinder data error: {e}")

                    # BoxScore doesn't include date, use LeagueGameFinder to get it
                    game_date = ""
                    home_team_id = home_team.get("teamId")
                    if home_team_id:
                        try:
                            from nba_api.stats.endpoints import leaguegamefinder

                            # Get games for the home team to find the date
                            if proxy:
                                gamefinder = leaguegamefinder.LeagueGameFinder(
                                    team_id_nullable=home_team_id, proxy=proxy
                                )
                            else:
                                gamefinder = leaguegamefinder.LeagueGameFinder(
                                    team_id_nullable=home_team_id
                                )
                            games_df = gamefinder.get_data_frames()[0]
                            matching_game = games_df[games_df["GAME_ID"] == game_id]
                            if not matching_game.empty:
                                game_date = matching_game.iloc[0]["GAME_DATE"]
                                logger.debug(
                                    f"Found game date via LeagueGameFinder: {game_date}"
                                )
                        except requests.exceptions.RequestException as e:
                            logger.debug(f"LeagueGameFinder network error: {e}")
                        except (KeyError, IndexError, TypeError, ValueError) as e:
                            logger.debug(f"LeagueGameFinder data error: {e}")

                    return {
                        "game_id": game_id,
                        "home_team": home_team_name,
                        "away_team": away_team_name,
                        "home_team_tricode": home_team.get("teamTricode", ""),
                        "away_team_tricode": away_team.get("teamTricode", ""),
                        "game_date": game_date,
                        "game_time_utc": "",
                    }
                else:
                    logger.debug(
                        f"BoxScore returned data but team names are empty for game_id={game_id}"
                    )
        else:
            logger.debug(
                f"BoxScore returned no data or missing 'boxScoreTraditional' for game_id={game_id}"
            )
    except requests.exceptions.RequestException as e:
        # BoxScore failed due to network error, will try Scoreboard fallback
        logger.debug(f"BoxScore network error for game_id={game_id}: {e}")
    except json.JSONDecodeError as e:
        # BoxScore returned invalid JSON, will try Scoreboard fallback
        logger.debug(f"BoxScore JSON error for game_id={game_id}: {e}")
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        # BoxScore data parsing failed, will try Scoreboard fallback
        # AttributeError occurs when nba_api tries to parse empty team stats
        # for games that haven't started yet
        logger.debug(f"BoxScore data error for game_id={game_id}: {e}")

    # Strategy 2: Fallback to Scoreboard search for future/upcoming games
    # Search 7 days back and 14 days forward for scheduled/recent games
    # Search 7 days back and 14 days forward for scheduled/recent games
    logger.debug(
        f"BoxScore lookup unsuccessful, searching Scoreboard for game_id={game_id}"
    )
    today = datetime.now().date()

    # Build list of dates to search: 7 days back, then 14 days forward
    dates_to_search = []
    # Past dates (7 days back, most recent first)
    for i in range(1, 8):
        dates_to_search.append(today - timedelta(days=i))
    # Today and future dates (14 days forward)

    # Build list of dates to search: 7 days back, then 14 days forward
    dates_to_search = []
    # Past dates (7 days back, most recent first)
    for i in range(1, 8):
        dates_to_search.append(today - timedelta(days=i))
    # Today and future dates (14 days forward)
    for i in range(14):
        dates_to_search.append(today + timedelta(days=i))

    # Prioritize: today first, then nearby dates
    # Sort by distance from today
    dates_to_search.sort(key=lambda d: abs((d - today).days))

    for search_date in dates_to_search:
        date_str = search_date.strftime("%Y-%m-%d")
        dates_to_search.append(today + timedelta(days=i))

    # Prioritize: today first, then nearby dates
    # Sort by distance from today
    dates_to_search.sort(key=lambda d: abs((d - today).days))

    for search_date in dates_to_search:
        date_str = search_date.strftime("%Y-%m-%d")

        logger.debug(f"Searching scoreboard for date={date_str}")

        try:
            if proxy:
                board = scoreboardv3.ScoreboardV3(game_date=date_str, proxy=proxy)
            else:
                board = scoreboardv3.ScoreboardV3(game_date=date_str)

            games_data = board.get_dict()
            if not games_data or "scoreboard" not in games_data:
                logger.debug(f"No scoreboard data for date={date_str}")
                continue

            scoreboard_data = games_data["scoreboard"]
            games_list = scoreboard_data.get("games", [])

            logger.debug(f"Found {len(games_list)} games on {date_str}")

            # Search for the game_id
            for game_data in games_list:
                if str(game_data.get("gameId", "")) == str(game_id):
                    # Found the game!
                    home_team = game_data.get("homeTeam", {})
                    away_team = game_data.get("awayTeam", {})

                    # Build full team name
                    home_team_name = f"{home_team.get('teamCity', '')} {home_team.get('teamName', '')}".strip()
                    away_team_name = f"{away_team.get('teamCity', '')} {away_team.get('teamName', '')}".strip()

                    game_time_utc = game_data.get("gameTimeUTC", "")

                    logger.debug(
                        f"Found game via Scoreboard on {date_str}: {away_team.get('teamTricode')} @ {home_team.get('teamTricode')}"
                    )

                    return {
                        "game_id": game_id,
                        "home_team": home_team_name,
                        "away_team": away_team_name,
                        "home_team_tricode": home_team.get("teamTricode", ""),
                        "away_team_tricode": away_team.get("teamTricode", ""),
                        "game_date": date_str,
                        "game_time_utc": game_time_utc,
                    }
        except requests.exceptions.RequestException as e:
            # Continue to next date if this one fails due to network error
            logger.debug(f"Network error searching scoreboard for date={date_str}: {e}")
            continue
        except json.JSONDecodeError as e:
            # Continue to next date if JSON parsing fails
            logger.debug(f"JSON error searching scoreboard for date={date_str}: {e}")
            continue
        except (KeyError, TypeError, ValueError) as e:
            # Continue to next date if data parsing fails
            logger.debug(f"Data error searching scoreboard for date={date_str}: {e}")
            continue

    # Game not found in both BoxScore and Scoreboards
    # Game not found in both BoxScore and Scoreboards
    logger.debug(
        f"Game not found in both BoxScore and Scoreboard (searched 7 days back, 14 days forward) for game_id={game_id}"
    )
    return None


async def get_game_info_by_id_async(
    game_id: str, proxy: str | None = None
) -> dict[str, Any] | None:
    """Async version of get_game_info_by_id.

    Wraps the synchronous version in a thread pool to avoid blocking the event loop.

    Args:
        game_id: NBA.com game ID (e.g., '0022500290')
        proxy: Optional proxy URL (automatically set from DOJOZERO_PROXY_URL env var if not provided)

    Returns:
        Dictionary with game information or None if not found
    """
    import asyncio

    return await asyncio.to_thread(get_game_info_by_id, game_id, proxy)
