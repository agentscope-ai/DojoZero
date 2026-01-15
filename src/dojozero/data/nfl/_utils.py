"""NFL utility functions and constants."""

import os
from datetime import datetime, timezone

# ESPN NFL Team IDs to abbreviations
TEAM_ID_TO_ABBREV: dict[str, str] = {
    "1": "ATL",  # Atlanta Falcons
    "2": "BUF",  # Buffalo Bills
    "3": "CHI",  # Chicago Bears
    "4": "CIN",  # Cincinnati Bengals
    "5": "CLE",  # Cleveland Browns
    "6": "DAL",  # Dallas Cowboys
    "7": "DEN",  # Denver Broncos
    "8": "DET",  # Detroit Lions
    "9": "GB",  # Green Bay Packers
    "10": "TEN",  # Tennessee Titans
    "11": "IND",  # Indianapolis Colts
    "12": "KC",  # Kansas City Chiefs
    "13": "LV",  # Las Vegas Raiders
    "14": "LAR",  # Los Angeles Rams
    "15": "MIA",  # Miami Dolphins
    "16": "MIN",  # Minnesota Vikings
    "17": "NE",  # New England Patriots
    "18": "NO",  # New Orleans Saints
    "19": "NYG",  # New York Giants
    "20": "NYJ",  # New York Jets
    "21": "PHI",  # Philadelphia Eagles
    "22": "ARI",  # Arizona Cardinals
    "23": "PIT",  # Pittsburgh Steelers
    "24": "LAC",  # Los Angeles Chargers
    "25": "SF",  # San Francisco 49ers
    "26": "SEA",  # Seattle Seahawks
    "27": "TB",  # Tampa Bay Buccaneers
    "28": "WSH",  # Washington Commanders
    "29": "CAR",  # Carolina Panthers
    "30": "JAX",  # Jacksonville Jaguars
    "33": "BAL",  # Baltimore Ravens
    "34": "HOU",  # Houston Texans
}

# Abbreviation to full team name
ABBREV_TO_TEAM_NAME: dict[str, str] = {
    "ATL": "Atlanta Falcons",
    "BUF": "Buffalo Bills",
    "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",
    "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos",
    "DET": "Detroit Lions",
    "GB": "Green Bay Packers",
    "TEN": "Tennessee Titans",
    "IND": "Indianapolis Colts",
    "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders",
    "LAR": "Los Angeles Rams",
    "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",
    "NE": "New England Patriots",
    "NO": "New Orleans Saints",
    "NYG": "New York Giants",
    "NYJ": "New York Jets",
    "PHI": "Philadelphia Eagles",
    "ARI": "Arizona Cardinals",
    "PIT": "Pittsburgh Steelers",
    "LAC": "Los Angeles Chargers",
    "SF": "San Francisco 49ers",
    "SEA": "Seattle Seahawks",
    "TB": "Tampa Bay Buccaneers",
    "WSH": "Washington Commanders",
    "CAR": "Carolina Panthers",
    "JAX": "Jacksonville Jaguars",
    "BAL": "Baltimore Ravens",
    "HOU": "Houston Texans",
}

# Division mappings
DIVISIONS: dict[str, list[str]] = {
    "AFC East": ["BUF", "MIA", "NE", "NYJ"],
    "AFC North": ["BAL", "CIN", "CLE", "PIT"],
    "AFC South": ["HOU", "IND", "JAX", "TEN"],
    "AFC West": ["DEN", "KC", "LV", "LAC"],
    "NFC East": ["DAL", "NYG", "PHI", "WSH"],
    "NFC North": ["CHI", "DET", "GB", "MIN"],
    "NFC South": ["ATL", "CAR", "NO", "TB"],
    "NFC West": ["ARI", "LAR", "SF", "SEA"],
}


def get_proxy() -> str | None:
    """Get proxy configuration from environment variables.

    Returns:
        Proxy URL string, or None if not configured
    """
    return os.getenv("DOJOZERO_PROXY_URL")


def get_team_abbreviation(team_id: str) -> str:
    """Get team abbreviation from ESPN team ID.

    Args:
        team_id: ESPN team ID

    Returns:
        Team abbreviation (e.g., "KC") or empty string if not found
    """
    return TEAM_ID_TO_ABBREV.get(str(team_id), "")


def get_team_name(abbreviation: str) -> str:
    """Get full team name from abbreviation.

    Args:
        abbreviation: Team abbreviation (e.g., "KC")

    Returns:
        Full team name (e.g., "Kansas City Chiefs") or empty string if not found
    """
    return ABBREV_TO_TEAM_NAME.get(abbreviation.upper(), "")


def get_team_division(abbreviation: str) -> str:
    """Get team's division from abbreviation.

    Args:
        abbreviation: Team abbreviation (e.g., "KC")

    Returns:
        Division name (e.g., "AFC West") or empty string if not found
    """
    abbrev_upper = abbreviation.upper()
    for division, teams in DIVISIONS.items():
        if abbrev_upper in teams:
            return division
    return ""


def parse_iso_datetime(date_str: str) -> datetime:
    """Parse ISO format datetime string.

    Handles both 'Z' suffix and timezone offset formats.

    Args:
        date_str: ISO format datetime string

    Returns:
        datetime object with timezone

    Raises:
        ValueError: If date_str cannot be parsed
    """
    if not date_str:
        return datetime.now(timezone.utc)

    # Handle 'Z' suffix
    if date_str.endswith("Z"):
        date_str = date_str[:-1] + "+00:00"

    return datetime.fromisoformat(date_str)


def format_game_clock(seconds: float) -> str:
    """Format seconds as game clock (MM:SS).

    Args:
        seconds: Time in seconds

    Returns:
        Formatted time string (e.g., "12:34")
    """
    if seconds < 0:
        return "0:00"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def american_odds_to_probability(odds: int) -> float:
    """Convert American odds to implied probability.

    Args:
        odds: American odds (e.g., -110, +200)

    Returns:
        Implied probability (0.0 to 1.0)
    """
    if odds == 0:
        return 0.5
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


def probability_to_american_odds(prob: float) -> int:
    """Convert probability to American odds.

    Args:
        prob: Probability (0.0 to 1.0)

    Returns:
        American odds (e.g., -110, +200)
    """
    if prob <= 0 or prob >= 1:
        return 0
    if prob > 0.5:
        return int(-100 * prob / (1 - prob))
    else:
        return int(100 * (1 - prob) / prob)


def spread_to_favorite(spread: float, home_team: str, away_team: str) -> str:
    """Determine which team is favored based on spread.

    Args:
        spread: Point spread (positive = home favored)
        home_team: Home team name
        away_team: Away team name

    Returns:
        Name of favored team, or "Pick" if even
    """
    if spread > 0:
        return home_team
    elif spread < 0:
        return away_team
    return "Pick"
