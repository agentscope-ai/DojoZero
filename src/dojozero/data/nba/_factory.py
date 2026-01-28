"""NBA Store Factory: Creates NBAStore instances for trial contexts."""

from dojozero.betting._metadata import BettingTrialMetadata
from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.nba._api import NBAExternalAPI
from dojozero.data.nba._store import NBAStore


@register_store_factory("nba")
class NBAStoreFactory(StoreFactory):
    """Factory for creating NBAStore instances.

    Uses BettingTrialMetadata for type-safe access to:
        - espn_game_id: ESPN game ID (e.g., "401810490")
        - nba_poll_intervals: Optional poll intervals dict
    """

    def create_store(
        self,
        store_id: str,
        metadata: BettingTrialMetadata,
        hub: DataHub,
    ) -> DataStore:
        """Create and configure an NBAStore instance.

        Args:
            store_id: Unique identifier for the store
            metadata: Typed trial metadata with espn_game_id and optional poll intervals
            hub: DataHub to connect the store to

        Returns:
            Configured NBAStore connected to hub
        """
        # Direct attribute access - type-safe
        espn_game_id = metadata.espn_game_id
        poll_intervals = metadata.nba_poll_intervals

        api = NBAExternalAPI()

        if poll_intervals:
            store = NBAStore(
                store_id=store_id,
                api=api,
                poll_intervals=poll_intervals,
            )
        else:
            # Use default intervals: {"boxscore": 60.0, "play_by_play": 20.0}
            store = NBAStore(
                store_id=store_id,
                api=api,
            )

        # Set poll identifier - espn_game_id is used to fetch game data from ESPN API
        # game_date is used for trace emission metadata
        store.set_poll_identifier(
            {
                "espn_game_id": espn_game_id,
                "game_date": metadata.game_date,
            }
        )

        # Connect to hub
        hub.connect_store(store)

        return store


__all__ = ["NBAStoreFactory"]
