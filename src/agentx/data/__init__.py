"""Data infrastructure: Events, Facts, Stores, Processors, and DataHub."""

# Core base classes
from agentx.data._models import DataEvent, DataEventFactory, DataFact, register_event
from agentx.data._processors import CompositeProcessor, DataProcessor
from agentx.data._replay import ReplayCoordinator
from agentx.data._stores import DataStore, ExternalAPI
from agentx.data._hub import DataHub

# Domain-specific implementations
# Import all event classes to trigger auto-registration
from agentx.data.nba import (
    NBAExternalAPI,
    NBAStore,
    PlayByPlayEvent,
    RawPlayByPlayEvent,
)
from agentx.data.polymarket import (
    OddsChangeEvent,
    PolymarketAPI,
    PolymarketStore,
    RawOddsChangeEvent,
)
from agentx.data.websearch import (
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
    # NBA
    "PlayByPlayEvent",
    "RawPlayByPlayEvent",
    "NBAExternalAPI",
    "NBAStore",
    # Polymarket
    "OddsChangeEvent",
    "RawOddsChangeEvent",
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
