"""NFL Store Factory: Creates NFLStore instances for trial contexts."""

from typing import Any

from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.nfl._api import NFLExternalAPI
from dojozero.data.nfl._store import NFLStore


@register_store_factory("nfl")
class NFLStoreFactory(StoreFactory):
    """Factory for creating NFLStore instances.

    Required metadata:
        - event_id: ESPN event ID (e.g., "401671827")

    Optional metadata:
        - poll_intervals: Dict of endpoint -> interval in seconds
          Default: {"scoreboard": 60.0, "summary": 30.0, "plays": 10.0}
    """

    def get_required_metadata_keys(self) -> list[str]:
        """Return required metadata keys."""
        return ["event_id"]

    def create_store(
        self,
        store_id: str,
        metadata: dict[str, Any],
        hub: DataHub,
    ) -> DataStore:
        """Create and configure an NFLStore instance.

        Args:
            store_id: Unique identifier for the store
            metadata: Trial metadata containing:
                - event_id: ESPN event ID
                - poll_intervals: Optional custom poll intervals
            hub: DataHub to connect the store to

        Returns:
            Configured NFLStore connected to hub
        """
        event_id = metadata.get("event_id", "")
        poll_intervals = metadata.get("nfl_poll_intervals")

        api = NFLExternalAPI()

        if poll_intervals:
            store = NFLStore(
                store_id=store_id,
                api=api,
                poll_intervals=poll_intervals,
            )
        else:
            # Use default intervals: {"scoreboard": 60.0, "summary": 30.0, "plays": 10.0}
            store = NFLStore(
                store_id=store_id,
                api=api,
            )

        # Set poll identifier
        store.set_poll_identifier({"event_id": event_id})

        # Connect to hub
        hub.connect_store(store)

        return store


__all__ = ["NFLStoreFactory"]
