"""NBA-specific utility functions."""

import os
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

def get_proxy() -> str | None:
    """Get proxy configuration from environment variables.
    
    Returns:
        Proxy URL string, or None if not configured
    """
    return os.getenv("PROXY_URL")


def with_proxy(func: F) -> F:
    """Decorator to ensure PROXY_URL is set up for NBA API calls.
    
    This decorator:
    1. Checks if PROXY_URL is available
    2. Passes proxy parameter to functions that accept it (checks function signature)
    3. Handles ImportError if nba_api is not available
    
    Usage:
        @with_proxy
        def my_nba_function(game_id: str):
            from agentx.data.nba._utils import get_proxy
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
    
    return wrapper  # type: ignore[return-value]


# Common NBA team name variations for matching
# Keys are team tricodes (standardized NBA team identifiers)
# First element in each list is the full official team name
TEAM_NAME_VARIATIONS: dict[str, list[str]] = {
    "MIA": ["Miami Heat", "heat", "miami heat", "miami"],
    "ORL": ["Orlando Magic", "magic", "orlando magic", "orlando"],
    "LAL": ["Los Angeles Lakers", "lakers", "los angeles lakers", "la lakers"],
    "BOS": ["Boston Celtics", "celtics", "boston celtics", "boston"],
    "GSW": ["Golden State Warriors", "warriors", "golden state warriors", "golden state"],
    "PHX": ["Phoenix Suns", "suns", "phoenix suns", "phoenix"],
    "OKC": ["Oklahoma City Thunder", "thunder", "oklahoma city thunder", "oklahoma city"],
    "DET": ["Detroit Pistons", "pistons", "detroit pistons", "detroit"],
    "TOR": ["Toronto Raptors", "raptors", "toronto raptors", "toronto"],
    "CHI": ["Chicago Bulls", "bulls", "chicago bulls", "chicago"],
    "NYK": ["New York Knicks", "knicks", "new york knicks", "ny knicks"],
    "BKN": ["Brooklyn Nets", "nets", "brooklyn nets", "brooklyn"],
    "PHI": ["Philadelphia 76ers", "76ers", "sixers", "philadelphia 76ers", "philadelphia"],
    "ATL": ["Atlanta Hawks", "hawks", "atlanta hawks", "atlanta"],
    "CHA": ["Charlotte Hornets", "hornets", "charlotte hornets", "charlotte"],
    "CLE": ["Cleveland Cavaliers", "cavaliers", "cavs", "cleveland cavaliers", "cleveland"],
    "DAL": ["Dallas Mavericks", "mavericks", "mavs", "dallas mavericks", "dallas"],
    "DEN": ["Denver Nuggets", "nuggets", "denver nuggets", "denver"],
    "HOU": ["Houston Rockets", "rockets", "houston rockets", "houston"],
    "IND": ["Indiana Pacers", "pacers", "indiana pacers", "indiana"],
    "LAC": ["Los Angeles Clippers", "clippers", "la clippers", "los angeles clippers"],
    "MEM": ["Memphis Grizzlies", "grizzlies", "memphis grizzlies", "memphis"],
    "MIN": ["Minnesota Timberwolves", "timberwolves", "wolves", "minnesota timberwolves", "minnesota"],
    "NOP": ["New Orleans Pelicans", "pelicans", "new orleans pelicans", "new orleans"],
    "POR": ["Portland Trail Blazers", "trail blazers", "blazers", "portland trail blazers", "portland"],
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
def get_game_info_by_id(game_id: str, proxy: str | None = None) -> dict[str, Any] | None:
    """Get team names and game date for a given NBA game ID.
    
    This function searches for the game across recent dates (today and past 7 days)
    to find the game information.
    
    Args:
        game_id: NBA.com game ID (e.g., '0022500290')
        proxy: Optional proxy URL (automatically set from PROXY_URL env var if not provided)
        
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
        Returns None if game not found
    """
    from nba_api.stats.endpoints import scoreboardv3
    from dateutil import parser
    
    # Search across recent dates (today and past 30 days)
    search_dates = []
    today = datetime.now().date()
    for i in range(30):  # Today + 30 days back
        search_dates.append(today - timedelta(days=i))
    
    for date in search_dates:
        date_str = date.strftime('%Y-%m-%d')
        try:
            # Fetch scoreboard for this date
            if proxy:
                board = scoreboardv3.ScoreboardV3(game_date=date_str, proxy=proxy)
            else:
                board = scoreboardv3.ScoreboardV3(game_date=date_str)
            
            games_data = board.get_dict()
            if not games_data or 'scoreboard' not in games_data:
                continue
            
            scoreboard_data = games_data['scoreboard']
            games_list = scoreboard_data.get('games', [])
            
            # Search for the game_id
            for game_data in games_list:
                if str(game_data.get('gameId', '')) == str(game_id):
                    # Found the game!
                    home_team = game_data.get('homeTeam', {})
                    away_team = game_data.get('awayTeam', {})
                    
                    # Build full team name
                    home_team_name = f"{home_team.get('teamCity', '')} {home_team.get('teamName', '')}".strip()
                    away_team_name = f"{away_team.get('teamCity', '')} {away_team.get('teamName', '')}".strip()
                    
                    game_time_utc = game_data.get('gameTimeUTC', '')
                    
                    return {
                        'game_id': game_id,
                        'home_team': home_team_name,
                        'away_team': away_team_name,
                        'home_team_tricode': home_team.get('teamTricode', ''),
                        'away_team_tricode': away_team.get('teamTricode', ''),
                        'game_date': date_str,
                        'game_time_utc': game_time_utc,
                    }
        except Exception:
            # Continue to next date if this one fails
            continue
    
    # Game not found in recent dates
    return None

