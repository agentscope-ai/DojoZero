"""World Cup (FIFA soccer) data infrastructure components."""

from dojozero.data.world_cup._api import WorldCupExternalAPI
from dojozero.data.world_cup._events import (
    SoccerGamePlayerStats,
    SoccerPlayerMatchStats,
    SoccerTeamMatchStats,
    WorldCupGameUpdateEvent,
    WorldCupPlayEvent,
)
from dojozero.data.world_cup._factory import WorldCupStoreFactory
from dojozero.data.world_cup._store import WorldCupStore

# Re-export unified lifecycle events for convenience
from dojozero.data._models import (
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
)

__all__ = [
    "WorldCupExternalAPI",
    # World Cup–specific events
    "WorldCupPlayEvent",
    "WorldCupGameUpdateEvent",
    # Curated soccer stats models
    "SoccerTeamMatchStats",
    "SoccerPlayerMatchStats",
    "SoccerGamePlayerStats",
    # Unified lifecycle events (re-exported for convenience)
    "GameInitializeEvent",
    "GameStartEvent",
    "GameResultEvent",
    # Store
    "WorldCupStore",
    "WorldCupStoreFactory",
]
