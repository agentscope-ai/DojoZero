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

    Reads metadata from BaseBettingTrialMetadata (defined in dojozero.betting).

    Required metadata:
        - sport_type: Sport type ("nba" or "nfl")

    Optional metadata:
        - market_url: Direct Polymarket market URL
        - espn_game_id: ESPN game/event ID (used as game_id for polling)
        - home_tricode: Home team code (e.g., "LAL", "KC")
        - away_tricode: Away team code (e.g., "BOS", "SF")
        - game_date: Game date string (YYYY-MM-DD) for slug construction
        - polymarket_poll_intervals: Custom poll intervals
          Default: {"odds": 300.0} (5 minutes)
    """

    def get_required_metadata_keys(self) -> list[str]:
        """Return required metadata keys."""
        return ["sport_type"]

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

        # Get sport type (required)
        sport_type_raw = metadata.get("sport_type")
        if not sport_type_raw:
            raise ValueError(
                "PolymarketStoreFactory requires 'sport_type' in metadata "
                "(expected 'nba' or 'nfl')"
            )
        sport = str(sport_type_raw)

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
        identifier: dict[str, Any] = {}

        if metadata.get("espn_game_id"):
            identifier["espn_game_id"] = metadata["espn_game_id"]

        # Add team info for slug construction if market_url not provided
        if not market_url:
            away_tricode = metadata.get("away_tricode")
            home_tricode = metadata.get("home_tricode")
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
