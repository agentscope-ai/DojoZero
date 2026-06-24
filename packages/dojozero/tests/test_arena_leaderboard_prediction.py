from fastapi import FastAPI
from fastapi.testclient import TestClient

from dojozero.arena_server import _server
from dojozero.arena_server._cache import LandingPageCache
from dojozero.arena_server._endpoints import register_rest_endpoints
from dojozero.arena_server._server import ArenaServerState
from dojozero.betting import AgentInfo
from dojozero.core import LeaderboardEntry


def _entry(
    agent_id: str,
    prediction_score: float,
    accuracy: float,
    total_predictions: int,
) -> LeaderboardEntry:
    return LeaderboardEntry(
        agent=AgentInfo(agent_id=agent_id, persona=agent_id),
        winnings=0,
        winRate=0,
        totalBets=0,
        roi=0,
        sharpe=0,
        predictionScore=prediction_score,
        accuracy=accuracy,
        totalPredictions=total_predictions,
    )


def test_world_cup_leaderboard_sorts_by_prediction_metrics() -> None:
    cache = LandingPageCache()
    cache.set_leaderboard(
        [
            _entry(
                "low-score-high-accuracy",
                prediction_score=10,
                accuracy=95,
                total_predictions=3,
            ),
            _entry(
                "high-score-low-accuracy",
                prediction_score=20,
                accuracy=80,
                total_predictions=5,
            ),
        ],
        league="world_cup",
    )
    _server._server_state = ArenaServerState(
        trace_reader=object(),  # type: ignore[arg-type]
        cache=cache,
        refresher=object(),  # type: ignore[arg-type]
    )

    app = FastAPI()
    register_rest_endpoints(app)
    client = TestClient(app)

    try:
        score_response = client.get(
            "/api/leaderboard",
            params={
                "league": "WORLD_CUP",
                "sort_by": "prediction_score",
                "sort_order": "desc",
            },
        )
        assert score_response.status_code == 200
        score_rows = score_response.json()["leaderboard"]
        assert [row["agent"]["agent_id"] for row in score_rows] == [
            "high-score-low-accuracy",
            "low-score-high-accuracy",
        ]
        assert score_rows[0]["total_predictions"] == 5

        accuracy_response = client.get(
            "/api/leaderboard",
            params={
                "league": "WORLD_CUP",
                "sort_by": "accuracy",
                "sort_order": "desc",
            },
        )
        assert accuracy_response.status_code == 200
        accuracy_rows = accuracy_response.json()["leaderboard"]
        assert [row["agent"]["agent_id"] for row in accuracy_rows] == [
            "low-score-high-accuracy",
            "high-score-low-accuracy",
        ]
    finally:
        _server._server_state = None


def test_all_leaderboard_can_split_market_and_prediction_modes() -> None:
    cache = LandingPageCache()
    cache.set_leaderboard(
        [
            LeaderboardEntry(
                agent=AgentInfo(agent_id="prediction-agent", persona="prediction"),
                winnings=0,
                winRate=0,
                totalBets=0,
                roi=0,
                sharpe=0,
                predictionScore=200,
                accuracy=90,
                totalPredictions=10,
            ),
            LeaderboardEntry(
                agent=AgentInfo(agent_id="market-agent", persona="market"),
                winnings=50,
                winRate=60,
                totalBets=8,
                roi=10,
                sharpe=1.2,
            ),
        ]
    )
    _server._server_state = ArenaServerState(
        trace_reader=object(),  # type: ignore[arg-type]
        cache=cache,
        refresher=object(),  # type: ignore[arg-type]
    )

    app = FastAPI()
    register_rest_endpoints(app)
    client = TestClient(app)

    try:
        all_response = client.get(
            "/api/leaderboard",
            params={"sort_by": "sharpe", "sort_order": "desc"},
        )
        assert all_response.status_code == 200
        all_rows = all_response.json()["leaderboard"]
        assert all_rows[0]["agent"]["agent_id"] == "market-agent"

        market_response = client.get(
            "/api/leaderboard",
            params={"mode": "market", "sort_by": "sharpe", "sort_order": "desc"},
        )
        assert market_response.status_code == 200
        market_rows = market_response.json()["leaderboard"]
        assert [row["agent"]["agent_id"] for row in market_rows] == ["market-agent"]

        prediction_response = client.get(
            "/api/leaderboard",
            params={
                "mode": "prediction",
                "sort_by": "prediction_score",
                "sort_order": "desc",
            },
        )
        assert prediction_response.status_code == 200
        prediction_rows = prediction_response.json()["leaderboard"]
        assert [row["agent"]["agent_id"] for row in prediction_rows] == [
            "prediction-agent"
        ]
    finally:
        _server._server_state = None


def test_agent_profile_returns_sharpe_from_market_leaderboard() -> None:
    cache = LandingPageCache()
    cache.set_leaderboard(
        [
            LeaderboardEntry(
                agent=AgentInfo(agent_id="market-agent", persona="market"),
                winnings=50,
                winRate=60,
                totalBets=8,
                roi=10,
                sharpe=1.23,
            ),
        ]
    )
    _server._server_state = ArenaServerState(
        trace_reader=object(),  # type: ignore[arg-type]
        cache=cache,
        refresher=object(),  # type: ignore[arg-type]
    )

    app = FastAPI()
    register_rest_endpoints(app)
    client = TestClient(app)

    try:
        response = client.get("/api/agent/market-agent/profile")
        assert response.status_code == 200
        stats = response.json()["stats"]
        assert stats["sharpe"] == 1.23
    finally:
        _server._server_state = None
