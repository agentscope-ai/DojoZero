"""NFL data infrastructure.

This module provides data stores, events, and utilities for NFL game data
using the ESPN API.

Example usage:
    from dojozero.data.nfl import NFLStore

    # Create store
    store = NFLStore()

    # Set up polling for specific game
    store.set_poll_identifier({"event_id": "401671827"})

    # Start polling (call in async context)
    await store.start_polling()
"""

from dojozero.data.nfl._api import NFLExternalAPI
from dojozero.data.nfl._events import (
    NFLDriveEvent,
    NFLGameUpdateEvent,
    NFLPlayEvent,
    NFLPlayerStats,
    NFLTeamGameStats,
)
from dojozero.data.nfl._state_tracker import NFLGameStateTracker
from dojozero.data.nfl._store import NFLStore
from dojozero.data.nfl._factory import NFLStoreFactory
from dojozero.data.nfl._utils import (
    ABBREV_TO_TEAM_NAME,
    DIVISIONS,
    TEAM_ID_TO_ABBREV,
    american_odds_to_probability,
    format_game_clock,
    get_proxy,
    get_team_abbreviation,
    get_team_division,
    get_team_name,
    parse_iso_datetime,
    probability_to_american_odds,
    spread_to_favorite,
)

# Re-export unified lifecycle events for convenience
from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    OddsUpdateEvent,
)

__all__ = [
    # API
    "NFLExternalAPI",
    # Store
    "NFLStore",
    "NFLStoreFactory",
    # State Tracker
    "NFLGameStateTracker",
    # NFL-specific events
    "NFLDriveEvent",
    "NFLGameUpdateEvent",
    "NFLPlayEvent",
    # Supporting types
    "NFLPlayerStats",
    "NFLTeamGameStats",
    # Unified lifecycle events (re-exported for convenience)
    "GameInitializeEvent",
    "GameStartEvent",
    "GameResultEvent",
    "OddsUpdateEvent",
    # Utils
    "ABBREV_TO_TEAM_NAME",
    "DIVISIONS",
    "TEAM_ID_TO_ABBREV",
    "american_odds_to_probability",
    "format_game_clock",
    "get_proxy",
    "get_team_abbreviation",
    "get_team_division",
    "get_team_name",
    "parse_iso_datetime",
    "probability_to_american_odds",
    "spread_to_favorite",
]
