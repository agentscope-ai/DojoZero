"""NBA Store Factory: Creates NBAStore instances for trial contexts."""

from typing import Any

from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.nba._api import NBAExternalAPI
from dojozero.data.nba._store import NBAStore


@register_store_factory("nba")
class NBAStoreFactory(StoreFactory):
    """Factory for creating NBAStore instances.

    Required metadata:
        - espn_game_id: ESPN game ID (e.g., "401810490")

    Optional metadata:
        - poll_intervals: Dict of endpoint -> interval in seconds
          Default: {"boxscore": 60.0, "play_by_play": 20.0}
    """

    def get_required_metadata_keys(self) -> list[str]:
        """Return required metadata keys."""
        return ["espn_game_id"]

    def create_store(
        self,
        store_id: str,
        metadata: dict[str, Any],
        hub: DataHub,
    ) -> DataStore:
        """Create and configure an NBAStore instance.

        Args:
            store_id: Unique identifier for the store
            metadata: Trial metadata containing:
                - espn_game_id: ESPN game ID
                - poll_intervals: Optional custom poll intervals
            hub: DataHub to connect the store to

        Returns:
            Configured NBAStore connected to hub
        """
        espn_game_id = metadata.get("espn_game_id", "")
        poll_intervals = metadata.get("nba_poll_intervals")

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
        store.set_poll_identifier({"espn_game_id": espn_game_id})

        # Connect to hub
        hub.connect_store(store)

        return store


__all__ = ["NBAStoreFactory"]
