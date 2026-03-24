"""WebSearch Store Factory: Creates WebSearchStore instances for trial contexts."""

import logging

from dojozero.betting._metadata import BettingTrialMetadata
from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.websearch._api import WebSearchAPI
from dojozero.data.websearch._store import WebSearchStore

logger = logging.getLogger(__name__)


@register_store_factory("websearch")
class WebSearchStoreFactory(StoreFactory):
    """Factory for creating WebSearchStore instances.

    Uses BettingTrialMetadata for type-safe access to trial configuration.
    The WebSearchStore emits raw search events; typed event processing
    is handled by event classes via WebSearchEventMixin.from_web_search().
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
            metadata: Typed trial metadata
            hub: DataHub to connect the store to

        Returns:
            Configured WebSearchStore connected to hub
        """
        api = WebSearchAPI()
        store = WebSearchStore(
            store_id=store_id,
            api=api,
        )

        # Connect to hub
        hub.connect_store(store)

        return store


__all__ = ["WebSearchStoreFactory"]
