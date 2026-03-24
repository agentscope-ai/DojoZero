"""NCAA-specific data infrastructure components."""

from dojozero.data.ncaa._api import NCAAExternalAPI
from dojozero.data.ncaa._events import (
    NCAAGamePlayerStats,
    NCAAGameUpdateEvent,
    NCAAPlayEvent,
    NCAAPlayerStats,
    NCAATeamGameStats,
)
from dojozero.data.ncaa._store import NCAAStore
from dojozero.data.ncaa._factory import NCAAStoreFactory
from dojozero.data.ncaa._utils import get_game_info_by_id_async

# Re-export unified lifecycle events for convenience
from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
)

__all__ = [
    "NCAAExternalAPI",
    "NCAAPlayEvent",
    "NCAAGameUpdateEvent",
    "NCAATeamGameStats",
    "NCAAPlayerStats",
    "NCAAGamePlayerStats",
    "GameInitializeEvent",
    "GameStartEvent",
    "GameResultEvent",
    "NCAAStore",
    "NCAAStoreFactory",
    "get_game_info_by_id_async",
]
