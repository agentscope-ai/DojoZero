"""Social Media Store Factory: Creates SocialMediaStore instances for trial contexts."""

from dojozero.betting._metadata import BettingTrialMetadata
from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.socialmedia._api import SocialMediaAPI
from dojozero.data.socialmedia._store import SocialMediaStore


@register_store_factory("socialmedia")
class SocialMediaStoreFactory(StoreFactory):
    """Factory for creating SocialMediaStore instances.

    Uses BettingTrialMetadata for type-safe access to trial configuration.
    The SocialMediaStore does not poll automatically; social media events are
    typically created via SocialMediaEventMixin.from_social_media() method in
    datastreams, not through store polling.
    """

    def create_store(
        self,
        store_id: str,
        metadata: BettingTrialMetadata,
        hub: DataHub,
    ) -> DataStore:
        """Create and configure a SocialMediaStore instance.

        Args:
            store_id: Unique identifier for the store
            metadata: Typed trial metadata
            hub: DataHub to connect the store to

        Returns:
            Configured SocialMediaStore connected to hub
        """
        api = SocialMediaAPI()
        store = SocialMediaStore(
            store_id=store_id,
            api=api,
        )

        # Connect to hub
        hub.connect_store(store)

        return store


__all__ = ["SocialMediaStoreFactory"]
