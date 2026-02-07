"""Hardcoded Super Bowl 60 game data for pre-game display.

This module provides a hardcoded game entry for Super Bowl 60 (SEA @ NE)
to be shown in the live games section before the actual game starts.
"""

from datetime import datetime, timezone

# Super Bowl 60: Seattle Seahawks @ New England Patriots
# February 8th, 2026 at 6:30pm ET (23:30 UTC)
SUPER_BOWL_KICKOFF_UTC = datetime(2026, 2, 8, 23, 30, tzinfo=timezone.utc)

SUPER_BOWL_GAME = {
    "id": "nfl-game-401772988-superbowl60",
    "league": "NFL",
    "home_team": {
        "team_id": "17",
        "name": "New England Patriots",
        "tricode": "NE",
        "location": "New England",
        "color": "002244",
        "alternate_color": "c60c30",
        "logo_url": "https://a.espncdn.com/i/teamlogos/nfl/500/scoreboard/ne.png",
        "record": "14-3",
        "players": [],
    },
    "away_team": {
        "team_id": "26",
        "name": "Seattle Seahawks",
        "tricode": "SEA",
        "location": "Seattle",
        "color": "002a5c",
        "alternate_color": "69be28",
        "logo_url": "https://a.espncdn.com/i/teamlogos/nfl/500/scoreboard/sea.png",
        "record": "14-3",
        "players": [],
    },
    "home_score": 0,
    "away_score": 0,
    "status": "upcoming",
    "date": "2026-02-08",
    "quarter": "",
    "clock": "",
    "bets": [],
    "winner": None,
    "win_amount": 0,
}


def should_show_superbowl_placeholder() -> bool:
    """Check if we should show the Super Bowl placeholder game.

    Returns True if current time is before Super Bowl kickoff (Feb 8, 2026 6:30pm ET).
    """
    now = datetime.now(timezone.utc)
    return now < SUPER_BOWL_KICKOFF_UTC
