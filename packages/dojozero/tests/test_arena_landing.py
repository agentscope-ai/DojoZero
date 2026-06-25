from fastapi import FastAPI
from fastapi.testclient import TestClient

from dojozero.arena_server import _server
from dojozero.arena_server._cache import LandingPageCache
from dojozero.arena_server._constants import SUPER_BOWL_GAME_ID
from dojozero.arena_server._endpoints import register_rest_endpoints
from dojozero.arena_server._models import GameCardData, GamesResponse, StatsResponse
from dojozero.arena_server._server import ArenaServerState
from dojozero.data import TeamIdentity


def test_nfl_landing_does_not_pin_completed_super_bowl_to_live_games() -> None:
    cache = LandingPageCache()
    super_bowl = GameCardData(
        id=SUPER_BOWL_GAME_ID,
        league="NFL",
        status="completed",
        date="2026-02-08T23:30:00+00:00",
        home_team=TeamIdentity(name="New England Patriots", tricode="NE"),
        away_team=TeamIdentity(name="Seattle Seahawks", tricode="SEA"),
        home_score=13,
        away_score=29,
    )
    cache.set_stats(StatsResponse(), league="nfl")
    cache.set_games(GamesResponse(completed_games=[super_bowl]), league="nfl")
    cache.set_agent_actions([], league="nfl")
    _server._server_state = ArenaServerState(
        trace_reader=object(),  # type: ignore[arg-type]
        cache=cache,
        refresher=object(),  # type: ignore[arg-type]
    )

    app = FastAPI()
    register_rest_endpoints(app)
    client = TestClient(app)

    try:
        response = client.get("/api/landing", params={"league": "NFL"})

        assert response.status_code == 200
        body = response.json()
        assert body["live_games"] == []
        assert [game["id"] for game in body["all_games"]] == [SUPER_BOWL_GAME_ID]
    finally:
        _server._server_state = None
