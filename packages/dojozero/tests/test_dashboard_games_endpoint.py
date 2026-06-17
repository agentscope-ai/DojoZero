"""Tests for the dashboard ``/api/games/world_cup`` discovery endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dojozero.dashboard_server._server import create_dashboard_app

_FETCHER = "dojozero.dashboard_server._game_discovery.WorldCupGameFetcher"


@pytest.fixture
def client():
    """Dashboard app with mocked dependencies.

    The endpoint's validation paths don't touch server state, so a TestClient
    without the lifespan context manager is enough.
    """
    app = create_dashboard_app(
        orchestrator=MagicMock(),
        scheduler_store=MagicMock(),
        no_scheduler=True,
        enable_gateway=False,
    )
    return TestClient(app)


def test_invalid_league_returns_400(client):
    resp = client.get("/api/games/world_cup", params={"league": "notaleague"})
    assert resp.status_code == 400
    assert "error" in resp.json()


@pytest.mark.parametrize(
    "params",
    [{"start_date": "2026-06-01"}, {"end_date": "2026-06-30"}],
)
def test_partial_date_range_returns_400(client, params):
    resp = client.get("/api/games/world_cup", params=params)
    assert resp.status_code == 400
    assert "start_date and end_date" in resp.json()["error"]


def test_upstream_fetch_error_returns_500(client):
    with patch(_FETCHER) as fetcher_cls:
        inst = fetcher_cls.return_value
        inst.league = "fifa.world"
        inst.fetch_games_for_date = AsyncMock(side_effect=RuntimeError("espn down"))
        resp = client.get("/api/games/world_cup")
    assert resp.status_code == 500
    assert "error" in resp.json()


def test_success_returns_games(client):
    game = MagicMock()
    game.to_dict.return_value = {"game_id": "760431", "status_text": "Second Half"}
    with patch(_FETCHER) as fetcher_cls:
        inst = fetcher_cls.return_value
        inst.league = "fifa.world"
        inst.fetch_games_for_date = AsyncMock(return_value=[game])
        resp = client.get("/api/games/world_cup")
    assert resp.status_code == 200
    body = resp.json()
    assert body["league"] == "fifa.world"
    assert body["games"] == [{"game_id": "760431", "status_text": "Second Half"}]
