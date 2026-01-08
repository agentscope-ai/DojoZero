"""NFL data infrastructure.

This module provides data stores, events, and utilities for NFL game data
using the ESPN API.

Example usage:
    from dojozero.data.nfl import NFLStore, NFLGameInitializeEvent

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
    NFLGameInitializeEvent,
    NFLGameResultEvent,
    NFLGameStartEvent,
    NFLGameUpdateEvent,
    NFLOddsUpdateEvent,
    NFLPlayEvent,
    NFLPlayerStats,
    NFLTeamStats,
)
from dojozero.data.nfl._state_tracker import NFLGameStateTracker
from dojozero.data.nfl._store import NFLStore
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

__all__ = [
    # API
    "NFLExternalAPI",
    # Store
    "NFLStore",
    # State Tracker
    "NFLGameStateTracker",
    # Events
    "NFLDriveEvent",
    "NFLGameInitializeEvent",
    "NFLGameResultEvent",
    "NFLGameStartEvent",
    "NFLGameUpdateEvent",
    "NFLOddsUpdateEvent",
    "NFLPlayEvent",
    # Supporting types
    "NFLPlayerStats",
    "NFLTeamStats",
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
