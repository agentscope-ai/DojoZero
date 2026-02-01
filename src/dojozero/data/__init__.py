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
    TotalOdds,
    SportEvent,
    StatsInsightEvent,
    PlayerIdentity,
    TeamIdentity,
    VenueInfo as SharedVenueInfo,
    WebSearchInsightEvent,
    extract_game_id,
    register_event,
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
    PreGameStatsEvent,
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
        PreGameStatsEvent,
        InjuryReportEvent,
        PowerRankingEvent,
        ExpertPredictionEvent,
    ],
    Field(discriminator="event_type"),
]

_data_event_adapter: TypeAdapter[AnyDataEvent] = TypeAdapter(AnyDataEvent)  # type: ignore[type-arg]


def deserialize_data_event(data: dict) -> DataEvent | None:
    """Deserialize a dict to a typed DataEvent via Pydantic discriminated union.

    Returns None if event_type is missing or unrecognized.
    """
    event_type = data.get("event_type")
    if not event_type:
        return None
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
    "PlayerIdentity",
    "TeamIdentity",
    "SharedVenueInfo",
    "OddsInfo",
    "MoneylineOdds",
    "SpreadOdds",
    "TotalOdds",
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
    "PreGameStatsEvent",
]
