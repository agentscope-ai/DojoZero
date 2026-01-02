"""Data infrastructure: Events, Facts, Stores, Processors, and DataHub."""

# Core base classes
from dojozero.data._models import DataEvent, DataEventFactory, DataFact, register_event
from dojozero.data._processors import CompositeProcessor, DataProcessor
from dojozero.data._replay import ReplayCoordinator
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data._hub import DataHub
from dojozero.data._streams import (
    DataHubDataStream,
    DataHubDataStreamConfig,
    StreamInitializer,
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
    "ReplayCoordinator",
    "DataHubDataStream",
    "DataHubDataStreamConfig",
    "StreamInitializer",
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
