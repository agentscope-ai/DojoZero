"""Data infrastructure: Events, Facts, Stores, Processors, and DataHub."""

# Core base classes
from dojozero.data._models import DataEvent, DataEventFactory, DataFact, register_event
from dojozero.data._processors import CompositeProcessor, DataProcessor
from dojozero.data._backtest import BacktestCoordinator, ReplayCoordinator
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data._hub import DataHub
from dojozero.data._streams import (
    DataHubDataStream,
    DataHubDataStreamConfig,
    StreamInitializer,
)
from dojozero.data._factory import (
    StoreFactory,
    register_store_factory,
    get_store_factory,
    list_store_factories,
    build_runtime_context,
)

# Domain-specific implementations
# Import all event classes to trigger auto-registration
from dojozero.data.nba import (
    NBAExternalAPI,
    NBAStore,
    PlayByPlayEvent,
)
from dojozero.data.polymarket import (
    OddsUpdateEvent,
    PolymarketAPI,
    PolymarketStore,
)
from dojozero.data.websearch import (
    ExpertPredictionEvent,
    InjurySummaryEvent,
    InjurySummaryProcessor,
    PowerRankingEvent,
    WebSearchAPI,
    WebSearchStore,
    RawWebSearchEvent,
)

# Event classes are auto-registered via @register_event decorator
# No manual registration needed

__all__ = [
    # Core base classes
    "DataEvent",
    "DataEventFactory",
    "DataFact",
    "register_event",
    "DataStore",
    "ExternalAPI",
    "DataProcessor",
    "CompositeProcessor",
    "DataHub",
    "BacktestCoordinator",
    "ReplayCoordinator",  # Deprecated alias
    "DataHubDataStream",
    "DataHubDataStreamConfig",
    "StreamInitializer",
    # Factory infrastructure
    "StoreFactory",
    "register_store_factory",
    "get_store_factory",
    "list_store_factories",
    "build_runtime_context",
    # NBA
    "PlayByPlayEvent",
    "NBAExternalAPI",
    "NBAStore",
    # Polymarket
    "OddsUpdateEvent",
    "PolymarketAPI",
    "PolymarketStore",
    # Web Search
    "RawWebSearchEvent",
    "InjurySummaryEvent",
    "PowerRankingEvent",
    "ExpertPredictionEvent",
    "InjurySummaryProcessor",
    "WebSearchAPI",
    "WebSearchStore",
]
