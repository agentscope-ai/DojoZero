"""Polymarket Store Factory: Creates PolymarketStore instances for trial contexts."""

from typing import Any

from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.polymarket._api import PolymarketAPI
from dojozero.data.polymarket._store import PolymarketStore


@register_store_factory("polymarket")
class PolymarketStoreFactory(StoreFactory):
    """Factory for creating PolymarketStore instances.

    Optional metadata:
        - market_url: Direct Polymarket market URL
        - game_id: Game identifier for NBA (used as event_id in odds events)
        - event_id: Event identifier for NFL (used as event_id in odds events)
        - away_team_tricode: Away team abbreviation (for slug construction)
        - home_team_tricode: Home team abbreviation (for slug construction)
        - game_date: Game date string (for slug construction)
        - polymarket_poll_intervals: Custom poll intervals
          Default: {"odds": 300.0} (5 minutes)
        - sample: Trial sample name (used to determine sport type: "nba-*" or "nfl-*")
    """

    def get_required_metadata_keys(self) -> list[str]:
        """Return required metadata keys."""
        return []  # All optional, but needs either market_url or team info

    def create_store(
        self,
        store_id: str,
        metadata: dict[str, Any],
        hub: DataHub,
    ) -> DataStore:
        """Create and configure a PolymarketStore instance.

        Args:
            store_id: Unique identifier for the store
            metadata: Trial metadata containing market info
            hub: DataHub to connect the store to

        Returns:
            Configured PolymarketStore connected to hub
        """
        # Get market URL if provided
        market_url_raw = metadata.get("market_url")
        market_url: str | None = (
            market_url_raw if isinstance(market_url_raw, str) else None
        )

        # Get poll intervals
        poll_intervals = metadata.get("polymarket_poll_intervals")

        # Determine sport type from sample name
        sample = str(metadata.get("sample", "nba"))
        if sample.startswith("nfl"):
            sport = "nfl"
        else:
            sport = "nba"  # Default to NBA

        api = PolymarketAPI()

        if poll_intervals:
            store = PolymarketStore(
                store_id=store_id,
                api=api,
                poll_intervals=poll_intervals,
                market_url=market_url,
                sport=sport,
            )
        else:
            # Use default intervals: {"odds": 300.0}
            store = PolymarketStore(
                store_id=store_id,
                api=api,
                market_url=market_url,
                sport=sport,
            )

        # Build identifier for polling
        # Support both NBA (game_id) and NFL (event_id) patterns
        identifier: dict[str, Any] = {}

        # Use game_id or event_id as the primary identifier
        if metadata.get("game_id"):
            identifier["game_id"] = metadata["game_id"]
        elif metadata.get("event_id"):
            identifier["game_id"] = metadata["event_id"]  # Use event_id as game_id

        # Add team info for slug construction if market_url not provided
        if not market_url:
            away_tricode = metadata.get("away_team_tricode")
            home_tricode = metadata.get("home_team_tricode")
            game_date = metadata.get("game_date")

            if away_tricode and isinstance(away_tricode, str):
                identifier["away_tricode"] = away_tricode
            if home_tricode and isinstance(home_tricode, str):
                identifier["home_tricode"] = home_tricode
            if game_date and isinstance(game_date, str):
                identifier["game_date"] = game_date

        store.set_poll_identifier(identifier)

        # Connect to hub
        hub.connect_store(store)

        return store


__all__ = ["PolymarketStoreFactory"]
