"""Polymarket Store Factory: Creates PolymarketStore instances for trial contexts."""

from typing import Any

from dojozero.betting._metadata import BettingTrialMetadata
from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.polymarket._api import PolymarketAPI
from dojozero.data.polymarket._store import PolymarketStore


@register_store_factory("polymarket")
class PolymarketStoreFactory(StoreFactory):
    """Factory for creating PolymarketStore instances.

    Uses BettingTrialMetadata for type-safe access to:
        - sport_type: Sport type ("nba" or "nfl") - required
        - market_url: Direct Polymarket market URL (optional)
        - espn_game_id: ESPN game/event ID (used as game_id for polling)
        - home_tricode: Home team code (e.g., "LAL", "KC")
        - away_tricode: Away team code (e.g., "BOS", "SF")
        - game_date: Game date string (YYYY-MM-DD) for slug construction
        - polymarket_poll_intervals: Custom poll intervals (optional)
    """

    def create_store(
        self,
        store_id: str,
        metadata: BettingTrialMetadata,
        hub: DataHub,
    ) -> DataStore:
        """Create and configure a PolymarketStore instance.

        Args:
            store_id: Unique identifier for the store
            metadata: Typed trial metadata containing market info
            hub: DataHub to connect the store to

        Returns:
            Configured PolymarketStore connected to hub
        """
        # Direct attribute access - type-safe
        market_url = metadata.market_url
        poll_intervals = metadata.polymarket_poll_intervals
        sport = metadata.sport_type

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

        identifier["espn_game_id"] = metadata.espn_game_id

        # Add team info for slug construction if market_url not provided
        if not market_url:
            if metadata.away_tricode:
                identifier["away_tricode"] = metadata.away_tricode
            if metadata.home_tricode:
                identifier["home_tricode"] = metadata.home_tricode
            if metadata.game_date:
                identifier["game_date"] = metadata.game_date

        store.set_poll_identifier(identifier)

        # Connect to hub
        hub.connect_store(store)

        return store


__all__ = ["PolymarketStoreFactory"]
