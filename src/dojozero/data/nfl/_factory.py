"""NFL Store Factory: Creates NFLStore instances for trial contexts."""

from dojozero.betting._metadata import BettingTrialMetadata
from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.nfl._api import NFLExternalAPI
from dojozero.data.nfl._store import NFLStore


@register_store_factory("nfl")
class NFLStoreFactory(StoreFactory):
    """Factory for creating NFLStore instances.

    Uses BettingTrialMetadata for type-safe access to:
        - espn_game_id: ESPN game ID (e.g., "401671827")
        - nfl_poll_intervals: Optional poll intervals dict
    """

    def create_store(
        self,
        store_id: str,
        metadata: BettingTrialMetadata,
        hub: DataHub,
    ) -> DataStore:
        """Create and configure an NFLStore instance.

        Args:
            store_id: Unique identifier for the store
            metadata: Typed trial metadata with espn_game_id and optional poll intervals
            hub: DataHub to connect the store to

        Returns:
            Configured NFLStore connected to hub
        """
        # Direct attribute access - type-safe
        espn_game_id = metadata.espn_game_id
        poll_intervals = metadata.nfl_poll_intervals

        api = NFLExternalAPI()

        if poll_intervals:
            store = NFLStore(
                store_id=store_id,
                api=api,
                poll_intervals=poll_intervals,
            )
        else:
            # Use default intervals (PRE_GAME profile)
            store = NFLStore(
                store_id=store_id,
                api=api,
            )

        # Set poll identifier - espn_game_id is used to fetch game data from ESPN API
        # game_date is used for trace emission metadata
        # dates (YYYYMMDD) is used to filter the scoreboard to the game's date
        poll_id: dict[str, str] = {
            "espn_game_id": espn_game_id,
            "game_date": metadata.game_date,
        }
        if metadata.game_date:
            # Convert YYYY-MM-DD to YYYYMMDD for ESPN scoreboard API
            poll_id["dates"] = metadata.game_date.replace("-", "")
        store.set_poll_identifier(poll_id)

        # Connect to hub
        hub.connect_store(store)

        return store


__all__ = ["NFLStoreFactory"]
