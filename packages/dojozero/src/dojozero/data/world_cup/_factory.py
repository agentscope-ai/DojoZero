"""World Cup Store Factory: creates ``WorldCupStore`` instances."""

from dojozero.betting._metadata import BettingTrialMetadata
from dojozero.data._factory import StoreFactory, register_store_factory
from dojozero.data._hub import DataHub
from dojozero.data._stores import DataStore
from dojozero.data.world_cup._api import DEFAULT_LEAGUE, WorldCupExternalAPI
from dojozero.data.world_cup._store import WorldCupStore


@register_store_factory("world_cup")
class WorldCupStoreFactory(StoreFactory):
    """Factory for ``WorldCupStore``.

    Reads from ``BettingTrialMetadata``:
        - espn_game_id: ESPN event ID
        - world_cup_league: FIFA league code (defaults to "fifa.world")
        - world_cup_poll_intervals: Optional poll intervals override
    """

    def create_store(
        self,
        store_id: str,
        metadata: BettingTrialMetadata,
        hub: DataHub,
    ) -> DataStore:
        espn_game_id = metadata.espn_game_id
        league = metadata.world_cup_league or DEFAULT_LEAGUE
        poll_intervals = metadata.world_cup_poll_intervals

        api = WorldCupExternalAPI(league=league)

        store = WorldCupStore(
            store_id=store_id,
            api=api,
            poll_intervals=poll_intervals,
            league=league,
        )

        store.set_poll_identifier(
            {
                "espn_game_id": espn_game_id,
                "game_date": metadata.game_date,
                "league": league,
            }
        )

        hub.connect_store(store)
        return store


__all__ = ["WorldCupStoreFactory"]
