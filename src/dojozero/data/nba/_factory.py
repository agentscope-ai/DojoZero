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
        - game_id: NBA game ID (e.g., "0022500477")

    Optional metadata:
        - poll_intervals: Dict of endpoint -> interval in seconds
          Default: {"scoreboard": 5.0, "play_by_play": 2.0}
    """

    def get_required_metadata_keys(self) -> list[str]:
        """Return required metadata keys."""
        return ["game_id"]

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
                - game_id: NBA game ID
                - poll_intervals: Optional custom poll intervals
            hub: DataHub to connect the store to

        Returns:
            Configured NBAStore connected to hub
        """
        game_id = metadata.get("game_id", "")
        poll_intervals = metadata.get("nba_poll_intervals")

        api = NBAExternalAPI()

        if poll_intervals:
            store = NBAStore(
                store_id=store_id,
                api=api,
                poll_intervals=poll_intervals,
            )
        else:
            # Use default intervals: {"scoreboard": 5.0, "play_by_play": 2.0}
            store = NBAStore(
                store_id=store_id,
                api=api,
            )

        # Set poll identifier
        store.set_poll_identifier({"game_id": game_id})

        # Connect to hub
        hub.connect_store(store)

        return store


__all__ = ["NBAStoreFactory"]
