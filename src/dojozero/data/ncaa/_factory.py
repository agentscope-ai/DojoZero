"""NCAA Store Factory: Creates NCAAStore instances for trial contexts."""

from dojozero.betting._metadata import BettingTrialMetadata
from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.ncaa._api import NCAAExternalAPI
from dojozero.data.ncaa._store import NCAAStore


@register_store_factory("ncaa")
class NCAAStoreFactory(StoreFactory):
    """Factory for creating NCAAStore instances.

    Uses BettingTrialMetadata for type-safe access to:
        - espn_game_id: ESPN game ID
        - ncaa_poll_intervals: Optional poll intervals dict
    """

    def create_store(
        self,
        store_id: str,
        metadata: BettingTrialMetadata,
        hub: DataHub,
    ) -> DataStore:
        """Create and configure an NCAAStore instance."""
        espn_game_id = metadata.espn_game_id
        poll_intervals = metadata.ncaa_poll_intervals

        api = NCAAExternalAPI()

        if poll_intervals:
            store = NCAAStore(
                store_id=store_id,
                api=api,
                poll_intervals=poll_intervals,
            )
        else:
            store = NCAAStore(
                store_id=store_id,
                api=api,
            )

        store.set_poll_identifier(
            {
                "espn_game_id": espn_game_id,
                "game_date": metadata.game_date,
            }
        )

        hub.connect_store(store)
        return store


__all__ = ["NCAAStoreFactory"]
