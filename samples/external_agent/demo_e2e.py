#!/usr/bin/env python3
"""End-to-end demo of the External Agent API.

This script demonstrates the complete flow:
1. Starts a mock trial with the HTTP gateway enabled
2. Runs an external agent that connects via the client SDK
3. Shows events flowing and bets being placed

Usage:
    python demo_e2e.py

    # If you have a proxy configured, bypass it for localhost:
    NO_PROXY=localhost,127.0.0.1 python demo_e2e.py

This is a self-contained demo that doesn't require a real game.
"""

import os

# Bypass proxy for localhost connections
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

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
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


async def run_mock_gateway(port: int = 8080) -> tuple[asyncio.Task, asyncio.Event]:
    """Start a mock gateway server for demo purposes.

    Returns:
        Tuple of (server_task, ready_event)
    """
    import uvicorn
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import StreamingResponse
    import json

    app = FastAPI(title="Demo Gateway")

    # Mock state
    state = {
        "agents": {},
        "sequence": 0,
        "betting_open": True,
        "home_prob": 0.55,
        "away_prob": 0.45,
        "bets": [],
    }

    @app.post("/api/v1/register")
    async def register(request: Request):
        data = await request.json()
        agent_id = data.get("agentId", "unknown")
        state["agents"][agent_id] = {
            "balance": data.get("initialBalance", 1000.0),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            "Agent '%s' registered with balance %.2f",
            agent_id,
            state["agents"][agent_id]["balance"],
        )
        return {
            "agentId": agent_id,
            "trialId": "demo-trial",
            "balance": state["agents"][agent_id]["balance"],
            "registeredAt": state["agents"][agent_id]["registered_at"],
        }

    @app.get("/api/v1/trial")
    async def get_trial():
        return {
            "trialId": "demo-trial",
            "phase": "running",
            "sportType": "nba",
            "gameId": "demo-game",
            "homeTeam": "Los Angeles Lakers",
            "awayTeam": "Boston Celtics",
            "gameTime": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/v1/odds/current")
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
            "eventId": "demo-game",
            "homeProbability": state["home_prob"],
            "awayProbability": state["away_prob"],
            "bettingOpen": state["betting_open"],
            "sequence": state["sequence"],
            "homeTeam": "Lakers",
            "awayTeam": "Celtics",
        }

    @app.post("/api/v1/bets")
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
            "Bet placed: %s on %s for %.2f (prob=%.2f)", bet_id, selection, amount, prob
        )
        return bet

    @app.get("/api/v1/bets")
    async def get_bets(request: Request):
        agent_id = request.headers.get("X-Agent-ID", "")
        agent_bets = [b for b in state["bets"] if b["agentId"] == agent_id]
        return {"bets": agent_bets}

    @app.get("/api/v1/balance")
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

    @app.get("/api/v1/events/stream")
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
                    "trialId": "demo-trial",
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
        return {"status": "ok", "trial_id": "demo-trial"}

    # Create server
    config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    ready_event = asyncio.Event()

    async def serve():
        # Start the server in background
        serve_task = asyncio.create_task(server.serve())
        # Wait for server to start
        while not server.started:
            await asyncio.sleep(0.1)
        ready_event.set()
        logger.info("Gateway started at http://127.0.0.1:%d", port)
        await serve_task

    task = asyncio.create_task(serve())
    return task, ready_event


async def run_demo_agent(gateway_url: str, agent_id: str):
    """Run a demo agent that connects and places bets."""
    from dojozero_client import DojoClient

    client = DojoClient()

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


async def main():
    """Run the end-to-end demo."""
    print("=" * 60)
    print("DojoZero External Agent API Demo")
    print("=" * 60)
    print()
    print("This demo shows:")
    print("  1. Gateway server starting with mock trial")
    print("  2. External agent connecting via SDK")
    print("  3. Real-time event streaming (SSE)")
    print("  4. Agent placing bets based on events")
    print()
    print("=" * 60)
    print()

    gateway_port = 18080  # Use higher port to avoid conflicts
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    agent_id = "demo-agent"

    # Start gateway
    logger.info("Starting mock gateway server...")
    gateway_task, ready_event = await run_mock_gateway(port=gateway_port)

    try:
        # Wait for gateway to be ready
        await asyncio.wait_for(ready_event.wait(), timeout=5.0)

        # Give server a moment to fully initialize
        await asyncio.sleep(1.0)

        # Test health endpoint
        import httpx

        async with httpx.AsyncClient() as test_client:
            try:
                resp = await test_client.get(f"{gateway_url}/health")
                logger.info(
                    "Health check response: status=%d, body=%s",
                    resp.status_code,
                    resp.text,
                )
            except Exception as e:
                logger.error("Health check failed: %s", e)
                raise

        # Run the agent
        await run_demo_agent(gateway_url, agent_id)

    except asyncio.TimeoutError:
        logger.error("Gateway failed to start")
    except KeyboardInterrupt:
        logger.info("Demo interrupted")
    finally:
        # Cleanup
        gateway_task.cancel()
        try:
            await gateway_task
        except asyncio.CancelledError:
            pass

    print("\nDemo finished!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
