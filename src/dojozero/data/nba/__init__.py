"""NBA-specific data infrastructure components."""

from dojozero.data.nba._api import NBAExternalAPI
from dojozero.data.nba._events import (
    NBAGamePlayerStats,
    NBAGameUpdateEvent,
    NBAPlayEvent,
    NBAPlayerStats,
    NBATeamGameStats,
)
from dojozero.data.nba._store import NBAStore
from dojozero.data.nba._utils import extract_team_names_from_query, normalize_team_name
from dojozero.data.nba._factory import NBAStoreFactory

# Re-export unified lifecycle events for convenience
from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
)

__all__ = [
    "NBAExternalAPI",
    # NBA-specific events
    "NBAPlayEvent",
    "NBAGameUpdateEvent",
    "NBATeamGameStats",
    "NBAPlayerStats",
    "NBAGamePlayerStats",
    # Unified lifecycle events (re-exported for convenience)
    "GameInitializeEvent",
    "GameStartEvent",
    "GameResultEvent",
    # Store
    "NBAStore",
    "NBAStoreFactory",
    # Utils
    "extract_team_names_from_query",
    "normalize_team_name",
]
