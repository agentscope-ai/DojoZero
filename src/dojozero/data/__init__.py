"""Data infrastructure: Events, Stores, Processors, and DataHub."""

from __future__ import annotations

import logging
from typing import Annotated, Union

from pydantic import Field, TypeAdapter

# Core base classes and hierarchy
from dojozero.data._models import (
    BaseGameUpdateEvent,
    BasePlayEvent,
    BaseSegmentEvent,
    DataEvent,
    EventTypes,
    GameEvent,
    GameInitializeEvent,
    GameResultEvent,
    GameStartEvent,
    MoneylineOdds,
    OddsInfo,
    OddsUpdateEvent,
    PreGameInsightEvent,
    SpreadOdds,
    SportEvent,
    StatsInsightEvent,
    TeamIdentity,
    VenueInfo as SharedVenueInfo,
    WebSearchInsightEvent,
    extract_game_id,
    register_event,
    register_legacy_event_type,
)
from dojozero.data._game_info import GameInfo, TeamInfo, VenueInfo
from dojozero.data._processors import CompositeProcessor, DataProcessor
from dojozero.data._backtest import BacktestCoordinator, ReplayCoordinator
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data._hub import DataHub
from dojozero.data._streams import (
    DataHubDataStream,
    DataHubDataStreamConfig,
)
from dojozero.data._factory import (
    StoreFactory,
    register_store_factory,
    get_store_factory,
    list_store_factories,
    build_runtime_context,
)
from dojozero.data._config import HubConfig, TrialDataStreamConfig

# Domain-specific implementations
from dojozero.data.nba import (
    NBAExternalAPI,
    NBAGameUpdateEvent,
    NBAPlayEvent,
    NBAStore,
)
from dojozero.data.nfl import (
    NFLDriveEvent,
    NFLGameUpdateEvent,
    NFLPlayEvent,
)
from dojozero.data.polymarket import (
    PolymarketAPI,
    PolymarketStore,
)
from dojozero.data.espn import (
    ESPNGameUpdateEvent,
    ESPNPlayEvent,
    HeadToHeadEvent,
    PlayerStatsEvent,
    RecentFormEvent,
    TeamStatsEvent,
)
from dojozero.data.websearch import (
    ExpertPredictionEvent,
    InjuryReportEvent,
    PowerRankingEvent,
    WebSearchAPI,
    WebSearchStore,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Pydantic Discriminated Union for all DataEvent subclasses
# =============================================================================

AnyDataEvent = Annotated[
    Union[
        GameInitializeEvent,
        GameStartEvent,
        GameResultEvent,
        OddsUpdateEvent,
        PreGameInsightEvent,
        WebSearchInsightEvent,
        StatsInsightEvent,
        NBAPlayEvent,
        NBAGameUpdateEvent,
        NFLPlayEvent,
        NFLDriveEvent,
        NFLGameUpdateEvent,
        ESPNGameUpdateEvent,
        ESPNPlayEvent,
        HeadToHeadEvent,
        TeamStatsEvent,
        PlayerStatsEvent,
        RecentFormEvent,
        InjuryReportEvent,
        PowerRankingEvent,
        ExpertPredictionEvent,
    ],
    Field(discriminator="event_type"),
]

_data_event_adapter: TypeAdapter[AnyDataEvent] = TypeAdapter(AnyDataEvent)  # type: ignore[type-arg]

# Legacy event_type strings → current canonical strings
_LEGACY_EVENT_TYPE_MAP: dict[str, str] = {
    "event.play_by_play": "event.nba_play",
    "event.game_update": "event.nba_game_update",
    "event.nfl_game_initialize": "event.game_initialize",
    "event.nfl_game_start": "event.game_start",
    "event.nfl_game_result": "event.game_result",
    "event.nfl_odds_update": "event.odds_update",
    "event.espn_game_initialize": "event.game_initialize",
    "event.espn_game_start": "event.game_start",
    "event.espn_game_end": "event.game_result",
    "event.espn_odds_update": "event.odds_update",
}


def deserialize_data_event(data: dict) -> DataEvent | None:
    """Deserialize a dict to a typed DataEvent via Pydantic discriminated union.

    Handles legacy event_type strings by mapping them to current canonical values.
    Returns None if event_type is missing or unrecognized.
    """
    event_type = data.get("event_type")
    if not event_type:
        return None
    if event_type in _LEGACY_EVENT_TYPE_MAP:
        data = {**data, "event_type": _LEGACY_EVENT_TYPE_MAP[event_type]}
    try:
        return _data_event_adapter.validate_python(data)
    except Exception:
        logger.debug("Failed to deserialize event: %s", event_type, exc_info=True)
        return None


__all__ = [
    # Core base classes
    "DataEvent",
    "EventTypes",
    "extract_game_id",
    "register_event",
    "register_legacy_event_type",
    # Discriminated union + deserializer
    "AnyDataEvent",
    "deserialize_data_event",
    # Event hierarchy
    "SportEvent",
    "GameEvent",
    "PreGameInsightEvent",
    "WebSearchInsightEvent",
    "StatsInsightEvent",
    "BasePlayEvent",
    "BaseSegmentEvent",
    "BaseGameUpdateEvent",
    # Unified lifecycle events
    "GameInitializeEvent",
    "GameStartEvent",
    "GameResultEvent",
    "OddsUpdateEvent",
    # Shared models
    "TeamIdentity",
    "SharedVenueInfo",
    "OddsInfo",
    "MoneylineOdds",
    "SpreadOdds",
    # Game info models (legacy)
    "GameInfo",
    "TeamInfo",
    "VenueInfo",
    # Infrastructure
    "DataStore",
    "ExternalAPI",
    "DataProcessor",
    "CompositeProcessor",
    "DataHub",
    "BacktestCoordinator",
    "ReplayCoordinator",
    "DataHubDataStream",
    "DataHubDataStreamConfig",
    # Factory infrastructure
    "StoreFactory",
    "register_store_factory",
    "get_store_factory",
    "list_store_factories",
    "build_runtime_context",
    # Shared configuration models
    "HubConfig",
    "TrialDataStreamConfig",
    # NBA
    "NBAPlayEvent",
    "NBAGameUpdateEvent",
    "NBAExternalAPI",
    "NBAStore",
    # NFL
    "NFLPlayEvent",
    "NFLDriveEvent",
    "NFLGameUpdateEvent",
    # ESPN
    "ESPNGameUpdateEvent",
    "ESPNPlayEvent",
    # Polymarket
    "PolymarketAPI",
    "PolymarketStore",
    # Web Search
    "InjuryReportEvent",
    "PowerRankingEvent",
    "ExpertPredictionEvent",
    "WebSearchAPI",
    "WebSearchStore",
    # Stats Insights (ESPN)
    "HeadToHeadEvent",
    "TeamStatsEvent",
    "PlayerStatsEvent",
    "RecentFormEvent",
]
