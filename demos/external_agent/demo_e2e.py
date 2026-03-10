#!/usr/bin/env python3
"""End-to-end demo of the External Agent API.

This script demonstrates the complete flow:
1. Starts a mock trial with the HTTP gateway enabled
2. Runs an external agent that connects via the client SDK
3. Shows events flowing and bets being placed

Usage:
    # Standalone mode (direct gateway connection):
    python demo_e2e.py

    # Dashboard mode (discovery + routing):
    python demo_e2e.py --dashboard

    # If you have a proxy configured, bypass it for localhost:
    NO_PROXY=localhost,127.0.0.1 python demo_e2e.py

This is a self-contained demo that doesn't require a real game.
"""

import os

# Bypass proxy for localhost connections
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# Add the project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(
    0, str(Path(__file__).parent.parent.parent / "packages" / "dojozero-client" / "src")
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.CRITICAL)
logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
logging.getLogger("uvicorn.access").setLevel(logging.CRITICAL)


def create_mock_gateway_app(trial_id: str = "demo-trial"):
    """Create a mock gateway FastAPI app.

    Args:
        trial_id: Trial ID for this gateway

    Returns:
        FastAPI app and state dict
    """
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import StreamingResponse

    app = FastAPI(title=f"Demo Gateway ({trial_id})")

    # Mock state
    state = {
        "trial_id": trial_id,
        "agents": {},
        "sequence": 0,
        "betting_open": True,
        "home_prob": 0.55,
        "away_prob": 0.45,
        "bets": [],
    }

    @app.post("/register")
    async def register(request: Request):
        data = await request.json()
        agent_id = data.get("agentId", "unknown")
        state["agents"][agent_id] = {
            "balance": data.get("initialBalance", 1000.0),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            "[%s] Agent '%s' registered with balance %.2f",
            trial_id,
            agent_id,
            state["agents"][agent_id]["balance"],
        )
        return {
            "agentId": agent_id,
            "trialId": trial_id,
            "balance": state["agents"][agent_id]["balance"],
            "registeredAt": state["agents"][agent_id]["registered_at"],
        }

    @app.get("/trial")
    async def get_trial():
        return {
            "trialId": trial_id,
            "phase": "running",
            "sportType": "nba",
            "gameId": f"game-{trial_id}",
            "homeTeam": "Los Angeles Lakers",
            "awayTeam": "Boston Celtics",
            "gameTime": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/odds/current")
    async def get_odds(request: Request):
        agent_id = request.headers.get("X-Agent-ID", "")
        if agent_id not in state["agents"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "NOT_REGISTERED",
                        "message": "Agent not registered",
                    }
                },
            )
        return {
            "eventId": f"game-{trial_id}",
            "homeProbability": state["home_prob"],
            "awayProbability": state["away_prob"],
            "bettingOpen": state["betting_open"],
            "sequence": state["sequence"],
            "homeTeam": "Lakers",
            "awayTeam": "Celtics",
        }

    @app.post("/bets")
    async def place_bet(request: Request):
        agent_id = request.headers.get("X-Agent-ID", "")
        if agent_id not in state["agents"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "NOT_REGISTERED",
                        "message": "Agent not registered",
                    }
                },
            )

        data = await request.json()
        amount = data.get("amount", 0)
        selection = data.get("selection", "home")

        agent = state["agents"][agent_id]
        if agent["balance"] < amount:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "INSUFFICIENT_BALANCE",
                        "message": "Not enough balance",
                    }
                },
            )

        agent["balance"] -= amount
        bet_id = f"bet-{len(state['bets']) + 1}"
        prob = state["home_prob"] if selection == "home" else state["away_prob"]

        bet = {
            "betId": bet_id,
            "agentId": agent_id,
            "market": data.get("market", "moneyline"),
            "selection": selection,
            "amount": amount,
            "probability": prob,
            "status": "pending",
            "placedAt": datetime.now(timezone.utc).isoformat(),
            "referenceSequence": data.get("referenceSequence", 0),
        }
        state["bets"].append(bet)
        logger.info(
            "[%s] Bet placed: %s on %s for %.2f (prob=%.2f)",
            trial_id,
            bet_id,
            selection,
            amount,
            prob,
        )
        return bet

    @app.get("/bets")
    async def get_bets(request: Request):
        agent_id = request.headers.get("X-Agent-ID", "")
        agent_bets = [b for b in state["bets"] if b["agentId"] == agent_id]
        return {"bets": agent_bets}

    @app.get("/balance")
    async def get_balance(request: Request):
        agent_id = request.headers.get("X-Agent-ID", "")
        if agent_id not in state["agents"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "NOT_REGISTERED",
                        "message": "Agent not registered",
                    }
                },
            )
        return {
            "agentId": agent_id,
            "balance": state["agents"][agent_id]["balance"],
            "holdings": {},
        }

    @app.get("/events/stream")
    async def stream_events(request: Request):
        agent_id = request.headers.get("X-Agent-ID", "")
        if agent_id not in state["agents"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "NOT_REGISTERED",
                        "message": "Agent not registered",
                    }
                },
            )

        async def event_generator():
            """Generate mock events."""
            import json

            events = [
                {"event_type": "event.game_start", "description": "Game started"},
                {
                    "event_type": "event.play",
                    "description": "LeBron James drives to the basket",
                },
                {
                    "event_type": "event.score",
                    "description": "Lakers score! 2 points",
                    "home_score": 2,
                    "away_score": 0,
                },
                {
                    "event_type": "event.play",
                    "description": "Jayson Tatum with the three-pointer",
                },
                {
                    "event_type": "event.score",
                    "description": "Celtics score! 3 points",
                    "home_score": 2,
                    "away_score": 3,
                },
                {
                    "event_type": "event.odds_update",
                    "description": "Odds updated",
                    "home_prob": 0.52,
                    "away_prob": 0.48,
                },
                {
                    "event_type": "event.play",
                    "description": "Anthony Davis blocks the shot",
                },
                {"event_type": "event.timeout", "description": "Celtics timeout"},
                {
                    "event_type": "event.play",
                    "description": "Austin Reaves hits the three!",
                },
                {
                    "event_type": "event.score",
                    "description": "Lakers score! 3 points",
                    "home_score": 5,
                    "away_score": 3,
                },
            ]

            for event_data in events:
                state["sequence"] += 1

                # Update odds if it's an odds event
                if "home_prob" in event_data:
                    state["home_prob"] = event_data["home_prob"]
                    state["away_prob"] = event_data["away_prob"]

                envelope = {
                    "trialId": trial_id,
                    "sequence": state["sequence"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": event_data,
                }

                yield f"event: event\ndata: {json.dumps(envelope)}\nid: {state['sequence']}\n\n"
                await asyncio.sleep(1.5)  # Simulate real-time events

            # Send end event
            yield 'event: trial_end\ndata: {"message": "Demo complete"}\n\n'

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.get("/health")
    async def health():
        return {"status": "ok", "trial_id": trial_id}

    return app, state


async def run_mock_gateway(port: int = 8080) -> tuple[asyncio.Task, asyncio.Event, Any]:
    """Start a mock gateway server for demo purposes (standalone mode).

    Returns:
        Tuple of (server_task, ready_event, server)
    """
    import uvicorn

    app, _state = create_mock_gateway_app("demo-trial")

    config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)

    ready_event = asyncio.Event()

    async def serve():
        serve_task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.1)
        ready_event.set()
        logger.info("Gateway started at http://127.0.0.1:%d", port)
        await serve_task

    task = asyncio.create_task(serve())
    return task, ready_event, server


async def run_mock_dashboard(
    port: int = 8000, trial_ids: list[str] | None = None
) -> tuple[asyncio.Task, asyncio.Event, Any]:
    """Start a mock dashboard server with gateway routing (dashboard mode).

    This simulates `dojo0 serve` by providing:
    - GET /api/gateways - List available trials
    - /api/trials/{trial_id}/... - Route to trial's gateway

    Returns:
        Tuple of (server_task, ready_event, server)
    """
    import uvicorn
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import StreamingResponse, JSONResponse

    if trial_ids is None:
        trial_ids = ["trial-alpha", "trial-beta"]

    # Create gateway apps for each trial
    trial_apps: dict[str, tuple[FastAPI, dict]] = {}
    for tid in trial_ids:
        app, state = create_mock_gateway_app(tid)
        trial_apps[tid] = (app, state)

    # Main dashboard app
    dashboard = FastAPI(title="Demo Dashboard")

    @dashboard.get("/api/gateways")
    async def list_gateways():
        """List available trial gateways."""
        gateways = [
            {"trial_id": tid, "endpoint": f"/api/trials/{tid}"} for tid in trial_ids
        ]
        return {"gateways": gateways, "count": len(gateways)}

    @dashboard.api_route(
        "/api/trials/{trial_id}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE"],
    )
    async def route_to_gateway(trial_id: str, path: str, request: Request):
        """Route requests to the appropriate trial gateway."""
        if trial_id not in trial_apps:
            raise HTTPException(status_code=404, detail=f"Trial {trial_id} not found")

        app, _state = trial_apps[trial_id]

        # Build the internal path
        internal_path = f"/{path}"
        if request.query_params:
            internal_path += f"?{request.query_params}"

        # Handle the request through the trial's app using httpx ASGI transport
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Forward the request
            body = await request.body()
            headers = dict(request.headers)
            headers.pop("host", None)

            response = await client.request(
                method=request.method,
                url=internal_path,
                headers=headers,
                content=body,
            )

            # Check if it's an SSE stream
            if "text/event-stream" in response.headers.get("content-type", ""):
                # For SSE, we need to stream the response
                async def stream_sse():
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://test"
                    ) as stream_client:
                        async with stream_client.stream(
                            method=request.method,
                            url=internal_path,
                            headers=headers,
                        ) as stream_response:
                            async for chunk in stream_response.aiter_bytes():
                                yield chunk

                return StreamingResponse(
                    stream_sse(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    },
                )

            return JSONResponse(
                content=response.json() if response.content else None,
                status_code=response.status_code,
            )

    @dashboard.get("/health")
    async def health():
        return {"status": "ok", "mode": "dashboard", "trials": trial_ids}

    config = uvicorn.Config(
        app=dashboard, host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)

    ready_event = asyncio.Event()

    async def serve():
        serve_task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.1)
        ready_event.set()
        logger.info(
            "Dashboard started at http://127.0.0.1:%d with trials: %s", port, trial_ids
        )
        await serve_task

    task = asyncio.create_task(serve())
    return task, ready_event, server


async def run_demo_agent_standalone(gateway_url: str, agent_id: str):
    """Run a demo agent in standalone mode (direct gateway connection)."""
    from dojozero_client import DojoClient

    client = DojoClient()

    logger.info("[Standalone] Agent connecting to %s...", gateway_url)
    await _run_agent_session(client, gateway_url, agent_id)


async def run_demo_agent_dashboard(dashboard_url: str, agent_id: str):
    """Run a demo agent in dashboard mode (discovery + routing)."""
    from dojozero_client import DojoClient

    client = DojoClient(dashboard_urls=[dashboard_url])

    # Step 1: Discover available trials
    logger.info("[Dashboard] Discovering trials from %s...", dashboard_url)
    gateways = await client.discover_trials()

    print("\n" + "=" * 60)
    print("TRIAL DISCOVERY")
    print("=" * 60)
    print(f"Found {len(gateways)} trial(s):")
    for g in gateways:
        print(f"  - {g.trial_id}: {g.url}")
    print("=" * 60 + "\n")

    if not gateways:
        logger.error("No trials available")
        return

    # Step 2: Select a trial (pick first one for demo)
    selected = gateways[0]
    logger.info(
        "[Dashboard] Selected trial: %s (of %d available)",
        selected.trial_id,
        len(gateways),
    )

    # Step 3: Connect using discovered URL
    gateway_url = selected.url
    if not gateway_url:
        logger.error("Selected trial has no URL")
        return
    logger.info("[Dashboard] Connecting via %s...", gateway_url)
    await _run_agent_session(client, gateway_url, agent_id)


async def _run_agent_session(client: Any, gateway_url: str, agent_id: str):
    """Common agent session logic for both modes."""

    logger.info("Agent connecting to %s...", gateway_url)

    async with client.connect_trial(
        gateway_url=gateway_url,
        agent_id=agent_id,
        persona="Demo betting agent",
        initial_balance=1000.0,
    ) as trial:
        metadata = await trial.get_trial_metadata()
        logger.info(
            "Connected to trial: %s vs %s", metadata.away_team, metadata.home_team
        )

        balance = await trial.get_balance()
        logger.info("Starting balance: $%.2f", balance.balance)

        bets_placed = 0

        logger.info("Subscribing to events...")
        print("\n" + "=" * 60)
        print("LIVE EVENT STREAM")
        print("=" * 60)

        async for event in trial.events():
            event_type = event.payload.get("event_type", "unknown")
            description = event.payload.get("description", "")

            # Print the event
            print(f"[{event.sequence:3d}] {event_type}: {description}")

            # Check if we should bet (bet on score events)
            if "score" in event_type or "odds" in event_type:
                odds = await trial.get_current_odds()

                if odds.betting_open and bets_placed < 3:  # Limit bets for demo
                    # Bet on the favorite
                    selection = "home" if odds.home_probability > 0.5 else "away"

                    try:
                        result = await trial.place_bet(
                            market="moneyline",
                            selection=selection,
                            amount=50.0,
                            reference_sequence=event.sequence,
                        )
                        bets_placed += 1
                        print(
                            f"       → BET PLACED: ${result.amount:.2f} on {selection} (prob={result.probability:.2%})"
                        )
                    except Exception as e:
                        print(f"       → Bet failed: {e}")

        # Final summary
        print("=" * 60)
        print("DEMO COMPLETE")
        print("=" * 60)

        final_balance = await trial.get_balance()
        bets = await trial.get_bets()

        print(f"Final balance: ${final_balance.balance:.2f}")
        print(f"Bets placed: {len(bets)}")
        for bet in bets:
            print(f"  - {bet.bet_id}: ${bet.amount:.2f} on {bet.selection}")
        print("=" * 60)


async def main_standalone():
    """Run standalone mode demo (direct gateway connection)."""
    print("=" * 60)
    print("DojoZero External Agent API Demo - STANDALONE MODE")
    print("=" * 60)
    print()
    print("This demo shows:")
    print("  1. Gateway server starting with mock trial")
    print("  2. External agent connecting directly via SDK")
    print("  3. Real-time event streaming (SSE)")
    print("  4. Agent placing bets based on events")
    print()
    print("=" * 60)
    print()

    gateway_port = 18080
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    agent_id = "demo-agent"

    # Start gateway
    logger.info("Starting mock gateway server...")
    gateway_task, ready_event, server = await run_mock_gateway(port=gateway_port)

    try:
        await asyncio.wait_for(ready_event.wait(), timeout=5.0)
        await asyncio.sleep(0.5)

        # Health check
        async with httpx.AsyncClient() as test_client:
            resp = await test_client.get(f"{gateway_url}/health")
            logger.info("Health check: %s", resp.json())

        # Run the agent
        await run_demo_agent_standalone(gateway_url, agent_id)

    except asyncio.TimeoutError:
        logger.error("Gateway failed to start")
    except KeyboardInterrupt:
        logger.info("Demo interrupted")
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(gateway_task, timeout=2.0)
        except asyncio.TimeoutError:
            gateway_task.cancel()
            try:
                await gateway_task
            except asyncio.CancelledError:
                pass

    print("\nDemo finished!")


async def main_dashboard():
    """Run dashboard mode demo (discovery + routing)."""
    print("=" * 60)
    print("DojoZero External Agent API Demo - DASHBOARD MODE")
    print("=" * 60)
    print()
    print("This demo shows:")
    print("  1. Dashboard server starting with multiple mock trials")
    print("  2. External agent discovering trials via SDK")
    print("  3. Agent connecting through dashboard routing")
    print("  4. Real-time event streaming (SSE)")
    print("  5. Agent placing bets based on events")
    print()
    print("=" * 60)
    print()

    dashboard_port = 18000
    dashboard_url = f"http://127.0.0.1:{dashboard_port}"
    agent_id = "demo-agent"

    # Start dashboard with multiple trials
    logger.info("Starting mock dashboard server with multiple trials...")
    dashboard_task, ready_event, server = await run_mock_dashboard(
        port=dashboard_port, trial_ids=["trial-alpha", "trial-beta"]
    )

    try:
        await asyncio.wait_for(ready_event.wait(), timeout=5.0)
        await asyncio.sleep(0.5)

        # Health check
        async with httpx.AsyncClient() as test_client:
            resp = await test_client.get(f"{dashboard_url}/health")
            logger.info("Health check: %s", resp.json())

        # Run the agent with discovery
        await run_demo_agent_dashboard(dashboard_url, agent_id)

    except asyncio.TimeoutError:
        logger.error("Dashboard failed to start")
    except KeyboardInterrupt:
        logger.info("Demo interrupted")
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(dashboard_task, timeout=2.0)
        except asyncio.TimeoutError:
            dashboard_task.cancel()
            try:
                await dashboard_task
            except asyncio.CancelledError:
                pass

    print("\nDemo finished!")


def main():
    """Parse args and run the appropriate demo."""
    parser = argparse.ArgumentParser(
        description="End-to-end demo of the External Agent API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Standalone mode (direct gateway connection):
  python demo_e2e.py

  # Dashboard mode (discovery + routing):
  python demo_e2e.py --dashboard
        """,
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Run in dashboard mode (discovery + routing)",
    )
    args = parser.parse_args()

    try:
        if args.dashboard:
            asyncio.run(main_dashboard())
        else:
            asyncio.run(main_standalone())
    except KeyboardInterrupt:
        print("\nInterrupted")


if __name__ == "__main__":
    main()
