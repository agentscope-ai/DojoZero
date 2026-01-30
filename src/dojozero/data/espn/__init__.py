"""ESPN data infrastructure module.

Provides generic ESPN API integration for any ESPN-supported sport/league.

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
    from dojozero.data.espn import ESPNStore, ESPNExternalAPI

    # Create a store for any sport
    nfl_store = ESPNStore(sport="football", league="nfl")
    nba_store = ESPNStore(sport="basketball", league="nba")
    epl_store = ESPNStore(sport="soccer", league="eng.1")

    # Use the API directly
    api = ESPNExternalAPI(sport="football", league="nfl")
    scoreboard = await api.fetch("scoreboard")
"""

from dojozero.data.espn._api import ESPNExternalAPI, get_espn_game_url, get_proxy
from dojozero.data.espn._events import (
    ESPNGameUpdateEvent,
    ESPNPlayEvent,
)
from dojozero.data.espn._state_tracker import ESPNStateTracker
from dojozero.data.espn._stats_events import (
    HeadToHeadEvent,
    PlayerStatsEvent,
    RecentFormEvent,
    TeamStatsEvent,
)
from dojozero.data.espn._store import ESPNStore

# Game status constants (re-exported from ESPNStateTracker for convenience)
STATUS_SCHEDULED = ESPNStateTracker.STATUS_SCHEDULED
STATUS_IN_PROGRESS = ESPNStateTracker.STATUS_IN_PROGRESS
STATUS_FINAL = ESPNStateTracker.STATUS_FINAL
STATUS_POSTPONED = ESPNStateTracker.STATUS_POSTPONED
STATUS_CANCELLED = ESPNStateTracker.STATUS_CANCELLED

__all__ = [
    # API
    "ESPNExternalAPI",
    "get_proxy",
    "get_espn_game_url",
    # Store
    "ESPNStore",
    # State Tracker
    "ESPNStateTracker",
    # Status Constants
    "STATUS_SCHEDULED",
    "STATUS_IN_PROGRESS",
    "STATUS_FINAL",
    "STATUS_POSTPONED",
    "STATUS_CANCELLED",
    # Events (generic ESPN, lifecycle events are now unified in _models)
    "ESPNGameUpdateEvent",
    "ESPNPlayEvent",
    # Stats Insight Events
    "HeadToHeadEvent",
    "TeamStatsEvent",
    "PlayerStatsEvent",
    "RecentFormEvent",
]
