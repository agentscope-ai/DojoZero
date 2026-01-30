"""ESPN data infrastructure module.

Provides the ESPN API layer for sport-specific stores (NBA, NFL, etc.).

Supported sports and leagues:
    - football/nfl, football/college-football
    - basketball/nba, basketball/mens-college-basketball, basketball/wnba
    - baseball/mlb
    - hockey/nhl
    - soccer/eng.1, soccer/usa.1, soccer/esp.1, etc.
    - tennis/atp, tennis/wta
    - golf/pga
    - mma/ufc
    - racing/f1

Example:
    from dojozero.data.espn import ESPNExternalAPI

    # Use the API directly
    api = ESPNExternalAPI(sport="football", league="nfl")
    scoreboard = await api.fetch("scoreboard")
"""

from dojozero.data.espn._api import ESPNExternalAPI, get_espn_game_url, get_proxy
from dojozero.data.espn._stats_events import (
    HeadToHeadEvent,
    PlayerStatsEvent,
    RecentFormEvent,
    TeamStatsEvent,
)

# Game status constants (used by scheduler for game lifecycle detection)
STATUS_SCHEDULED = 1
STATUS_IN_PROGRESS = 2
STATUS_FINAL = 3
STATUS_POSTPONED = 4
STATUS_CANCELLED = 5

__all__ = [
    # API
    "ESPNExternalAPI",
    "get_proxy",
    "get_espn_game_url",
    # Status Constants
    "STATUS_SCHEDULED",
    "STATUS_IN_PROGRESS",
    "STATUS_FINAL",
    "STATUS_POSTPONED",
    "STATUS_CANCELLED",
    # Stats Insight Events
    "HeadToHeadEvent",
    "TeamStatsEvent",
    "PlayerStatsEvent",
    "RecentFormEvent",
]
