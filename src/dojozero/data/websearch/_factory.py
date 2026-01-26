"""WebSearch Store Factory: Creates WebSearchStore instances for trial contexts."""

import logging
from typing import Any

from dojozero.betting._metadata import BettingTrialMetadata
from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.websearch._api import WebSearchAPI
from dojozero.data.websearch._processors import (
    ExpertPredictionProcessor,
    InjurySummaryProcessor,
    PowerRankingProcessor,
)
from dojozero.data.websearch._store import WebSearchStore

logger = logging.getLogger(__name__)


# Default processor mapping for common event types
# Maps event_type -> (processor_class, source_event_types)
DEFAULT_PROCESSOR_MAP: dict[str, tuple[type[Any] | None, list[str]]] = {
    # Raw stream: no processor, emitted directly from store
    "raw_web_search": (None, []),
    # Processed streams: processor class and source event types
    "injury_summary": (InjurySummaryProcessor, ["raw_web_search"]),
    "power_ranking": (PowerRankingProcessor, ["raw_web_search"]),
    "expert_prediction": (ExpertPredictionProcessor, ["raw_web_search"]),
}


@register_store_factory("websearch")
class WebSearchStoreFactory(StoreFactory):
    """Factory for creating WebSearchStore instances.

    Uses BettingTrialMetadata for type-safe access to trial configuration.
    The WebSearchStore does not require specific metadata fields from the
    betting metadata - it uses default processor mappings.
    """

    def create_store(
        self,
        store_id: str,
        metadata: BettingTrialMetadata,
        hub: DataHub,
    ) -> DataStore:
        """Create and configure a WebSearchStore instance.

        Args:
            store_id: Unique identifier for the store
            metadata: Typed trial metadata (used for consistency, store uses defaults)
            hub: DataHub to connect the store to

        Returns:
            Configured WebSearchStore connected to hub
        """
        api = WebSearchAPI()
        store = WebSearchStore(
            store_id=store_id,
            api=api,
        )

        # Use default event types and processor map
        event_types = ["injury_summary", "power_ranking", "expert_prediction"]
        processor_map = DEFAULT_PROCESSOR_MAP

        # Register processors for requested event types
        registered: set[str] = set()
        for event_type in event_types:
            if event_type in processor_map and event_type not in registered:
                processor_class, source_event_types = processor_map[event_type]
                processor = processor_class() if processor_class else None
                store.register_stream(event_type, processor, source_event_types)
                registered.add(event_type)
                logger.debug(
                    "Registered processor for event type '%s' on store '%s'",
                    event_type,
                    store_id,
                )

        # Connect to hub
        hub.connect_store(store)

        return store


__all__ = ["WebSearchStoreFactory", "DEFAULT_PROCESSOR_MAP"]
