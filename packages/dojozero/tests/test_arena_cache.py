from dojozero.arena_server._cache import CACHEABLE_LEAGUES, LandingPageCache
from dojozero.arena_server._models import GameCardData, GamesResponse, StatsResponse


def test_world_cup_uses_per_league_stats_and_games_cache() -> None:
    cache = LandingPageCache()

    global_stats = StatsResponse(games_played=1)
    world_cup_stats = StatsResponse(games_played=2)
    global_games = GamesResponse(
        upcoming_games=[GameCardData(id="global-game", league="NBA")]
    )
    world_cup_games = GamesResponse(
        upcoming_games=[GameCardData(id="world-cup-game", league="WORLD_CUP")]
    )

    cache.set_stats(global_stats)
    cache.set_games(global_games)
    cache.set_stats(world_cup_stats, league="world_cup")
    cache.set_games(world_cup_games, league="world_cup")

    assert "WORLD_CUP" in CACHEABLE_LEAGUES
    assert cache.get_stats() == global_stats
    assert cache.get_games() == global_games
    assert cache.get_stats(league="world_cup") == world_cup_stats
    assert cache.get_games(league="world_cup") == world_cup_games
