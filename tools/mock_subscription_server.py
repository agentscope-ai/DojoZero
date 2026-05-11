"""Lightweight mock server for testing subscription auto-join.

No dojozero server-side dependencies — pure FastAPI. Simulates:
- GET /api/gateways (trial discovery)
- GET /api/trials/{trial_id}/trial (trial metadata)
- POST /api/trials/{trial_id}/agents (agent registration)
- POST /api/trials/{trial_id}/agents/reconnect
- GET /api/trials/{trial_id}/balance
- GET /api/trials/{trial_id}/events/recent
- GET /api/trials/{trial_id}/trial/results
- POST /mock/add-trial, POST /mock/remove-trial, GET /mock/state

Usage:
    .venv/bin/python tools/mock_subscription_server.py [--port 8000]

Test session:
    # Terminal 1: start mock server
    .venv/bin/python tools/mock_subscription_server.py

    # Terminal 2: kill old daemon, configure, subscribe, start daemon
    dojozero-agent stop
    dojozero-agent config --dashboard-url http://localhost:8000
    dojozero-agent config --api-key test-key
    dojozero-agent subscribe --sport nba --forever
    dojozero-agent subscriptions list
    dojozero-agent daemon -b
    # Wait ~60s for watcher poll, then:
    dojozero-agent list

    # Add more trials dynamically:
    curl -X POST 'http://localhost:8000/mock/add-trial?sport=nfl&home=KC&away=BUF'
"""

import argparse
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("mock_server")


class MockTrial:
    """State for a single mock trial."""

    def __init__(
        self,
        trial_id: str,
        sport_type: str = "nba",
        home_team: str = "Los Angeles Lakers",
        away_team: str = "Boston Celtics",
        home_tricode: str = "LAL",
        away_tricode: str = "BOS",
    ):
        self.trial_id = trial_id
        self.sport_type = sport_type
        self.home_team = home_team
        self.away_team = away_team
        self.home_tricode = home_tricode
        self.away_tricode = away_tricode
        self.agents: dict[str, dict[str, Any]] = {}
        self.event_sequence = 0

    def to_metadata(self) -> dict[str, Any]:
        return {
            "trialId": self.trial_id,
            "phase": "running",
            "sportType": self.sport_type,
            "gameId": f"mock-{self.trial_id}",
            "homeTeam": self.home_team,
            "awayTeam": self.away_team,
            "gameTime": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "home_team_tricode": self.home_tricode,
                "away_team_tricode": self.away_tricode,
            },
        }


class MockServer:
    """Manages all mock trials."""

    def __init__(self) -> None:
        self.trials: dict[str, MockTrial] = {}
        self._agent_counter = 0

    def add_trial(
        self,
        sport: str = "nba",
        home: str = "Los Angeles Lakers",
        away: str = "Boston Celtics",
        home_tri: str = "LAL",
        away_tri: str = "BOS",
        trial_id: str | None = None,
    ) -> MockTrial:
        if trial_id is None:
            trial_id = f"mock-{sport}-{uuid.uuid4().hex[:6]}"
        trial = MockTrial(
            trial_id=trial_id,
            sport_type=sport,
            home_team=home,
            away_team=away,
            home_tricode=home_tri,
            away_tricode=away_tri,
        )
        self.trials[trial_id] = trial
        logger.info(
            "Added trial: %s (%s vs %s) [%s]", trial_id, home_tri, away_tri, sport
        )
        return trial

    def remove_trial(self, trial_id: str) -> bool:
        if trial_id in self.trials:
            del self.trials[trial_id]
            logger.info("Removed trial: %s", trial_id)
            return True
        return False

    def register_agent(self, trial_id: str, api_key: str) -> dict[str, Any]:
        trial = self.trials.get(trial_id)
        if trial is None:
            return {"error": "trial not found"}

        # Reuse agent if same key already registered
        for agent_id, info in trial.agents.items():
            if info.get("api_key") == api_key:
                return {
                    "agentId": agent_id,
                    "trialId": trial_id,
                    "sessionKey": info["session_key"],
                    "balance": 1000.0,
                    "registeredAt": info["registered_at"],
                    "authenticated": True,
                }

        self._agent_counter += 1
        agent_id = f"mock-agent-{self._agent_counter}"
        session_key = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        trial.agents[agent_id] = {
            "api_key": api_key,
            "session_key": session_key,
            "registered_at": now,
        }
        logger.info("Registered agent %s on trial %s", agent_id, trial_id)
        return {
            "agentId": agent_id,
            "trialId": trial_id,
            "sessionKey": session_key,
            "balance": 1000.0,
            "registeredAt": now,
            "authenticated": True,
        }


def build_app(port: int) -> FastAPI:
    server = MockServer()

    # Start with one NBA trial by default
    server.add_trial(
        sport="nba",
        home="Los Angeles Lakers",
        away="Boston Celtics",
        home_tri="LAL",
        away_tri="BOS",
        trial_id="mock-nba-lal-bos",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("=" * 60)
        logger.info("Mock Subscription Server ready!")
        logger.info("  Port: %d", port)
        logger.info("  Trials: %d", len(server.trials))
        for tid, t in server.trials.items():
            logger.info(
                "    %s: %s @ %s [%s]",
                tid,
                t.away_tricode,
                t.home_tricode,
                t.sport_type,
            )
        logger.info("")
        logger.info("Control:")
        logger.info("  curl http://localhost:%d/api/gateways", port)
        logger.info("  curl http://localhost:%d/mock/state", port)
        logger.info(
            "  curl -X POST 'http://localhost:%d/mock/add-trial?sport=nfl&home_tri=KC&away_tri=BUF'",
            port,
        )
        logger.info(
            "  curl -X POST 'http://localhost:%d/mock/remove-trial?trial_id=xxx'", port
        )
        logger.info("=" * 60)
        yield

    app = FastAPI(title="DojoZero Mock Subscription Server", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Discovery ---

    @app.get("/api/gateways")
    async def list_gateways():
        return {
            "gateways": [
                {"trial_id": tid, "endpoint": f"/api/trials/{tid}"}
                for tid in server.trials
            ],
            "count": len(server.trials),
        }

    # --- Per-trial gateway endpoints ---

    @app.get("/api/trials/{trial_id}/trial")
    async def get_trial_metadata(trial_id: str):
        trial = server.trials.get(trial_id)
        if trial is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return trial.to_metadata()

    @app.post("/api/trials/{trial_id}/agents")
    async def register_agent(trial_id: str, request: Request):
        body = await request.json()
        api_key = body.get("apiKey", "anonymous")
        result = server.register_agent(trial_id, api_key)
        if "error" in result:
            return JSONResponse(result, status_code=404)
        return result

    @app.post("/api/trials/{trial_id}/agents/reconnect")
    async def reconnect_agent(trial_id: str, request: Request):
        body = await request.json()
        api_key = body.get("apiKey", "")
        session_key = body.get("sessionKey", "")
        trial = server.trials.get(trial_id)
        if trial is None:
            return JSONResponse({"error": "trial not found"}, status_code=404)
        for agent_id, info in trial.agents.items():
            if info.get("session_key") == session_key:
                return {
                    "agentId": agent_id,
                    "trialId": trial_id,
                    "sessionKey": session_key,
                    "balance": 1000.0,
                    "registeredAt": info["registered_at"],
                    "authenticated": True,
                }
        # Fall back to fresh registration
        return server.register_agent(trial_id, api_key)

    @app.get("/api/trials/{trial_id}/balance")
    async def get_balance(trial_id: str, request: Request):
        agent_id = request.headers.get("X-Agent-ID", "unknown")
        return {
            "agentId": agent_id,
            "balance": 1000.0,
            "holdings": [],
        }

    @app.get("/api/trials/{trial_id}/events/recent")
    async def get_recent_events(trial_id: str):
        trial = server.trials.get(trial_id)
        seq = trial.event_sequence if trial else 0
        return {
            "events": [],
            "currentSequence": seq,
        }

    @app.get("/api/trials/{trial_id}/trial/results")
    async def get_results(trial_id: str):
        return {
            "trialId": trial_id,
            "status": "running",
            "results": [],
        }

    @app.delete("/api/trials/{trial_id}/agents/{agent_id}")
    async def unregister_agent(trial_id: str, agent_id: str):
        trial = server.trials.get(trial_id)
        if trial and agent_id in trial.agents:
            del trial.agents[agent_id]
        return {"status": "unregistered"}

    # --- Mock control ---

    @app.post("/mock/add-trial")
    async def mock_add_trial(
        sport: str = "nba",
        home: str = "Home Team",
        away: str = "Away Team",
        home_tri: str = "HOM",
        away_tri: str = "AWY",
        trial_id: str | None = None,
    ):
        trial = server.add_trial(sport, home, away, home_tri, away_tri, trial_id)
        return {"trial_id": trial.trial_id, "sport": sport}

    @app.post("/mock/remove-trial")
    async def mock_remove_trial(trial_id: str):
        removed = server.remove_trial(trial_id)
        return {"removed": removed, "trial_id": trial_id}

    @app.get("/mock/state")
    async def mock_state():
        return {
            "trials": {
                tid: {
                    "sport": t.sport_type,
                    "matchup": f"{t.away_tricode} @ {t.home_tricode}",
                    "agents": list(t.agents.keys()),
                }
                for tid, t in server.trials.items()
            },
            "total_trials": len(server.trials),
        }

    return app


def main():
    parser = argparse.ArgumentParser(
        description="Lightweight mock server for testing subscription auto-join"
    )
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()

    app = build_app(args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
