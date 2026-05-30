#!/usr/bin/env python3
"""Dual-mode demo: external agent connects to both betting and prediction trials.

Prerequisites:
    1. dojo0 serve is running (with trial_sources/daily/nba.yaml + nba-prediction.yaml)
    2. An agent key exists: dojo0 agents add --id demo-dual-agent

Usage:
    python demo_dual_mode.py
    python demo_dual_mode.py --dashboard http://localhost:8000
    python demo_dual_mode.py --api-key sk-agent-xxx

This demo discovers all running trials, identifies a betting and prediction
pair for the same game, then connects to both simultaneously. It places bets
on the betting trial and submits predictions on the prediction trial.
"""

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

# NO_PROXY must be set before any HTTP client builds its proxy config —
# import order via ruff/isort puts this after the stdlib imports above,
# and runtime ordering is still correct because the http clients live in
# dojozero_client (imported later).
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")

import httpx  # noqa: E402  (must follow NO_PROXY assignment)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("demo_dual_mode")
logging.getLogger("httpx").setLevel(logging.WARNING)


async def run_betting_session(
    client: Any,
    gateway_url: str,
    api_key: str,
    max_bets: int = 3,
) -> dict[str, Any]:
    """Run betting session: listen to events and place bets."""
    results: dict[str, Any] = {"mode": "betting", "bets_placed": 0, "events_seen": 0}

    async with client.connect_trial(gateway_url=gateway_url, api_key=api_key) as trial:
        metadata = await trial.get_trial_metadata()
        logger.info(
            "[BETTING] Connected: %s vs %s (trial: %s)",
            metadata.away_team,
            metadata.home_team,
            trial.trial_id,
        )

        results["trial_id"] = trial.trial_id
        results["home_team"] = metadata.home_team
        results["away_team"] = metadata.away_team

        try:
            balance = await trial.get_balance()
            logger.info("[BETTING] Starting balance: $%.2f", balance.balance)
            results["starting_balance"] = balance.balance
        except Exception as e:
            logger.warning("[BETTING] Could not get balance: %s", e)

        bets_placed = 0

        logger.info("[BETTING] Listening to events...")
        async for event in trial.events():
            results["events_seen"] += 1
            event_type = event.payload.get("event_type", "unknown")

            if "odds" in event_type or "game_update" in event_type:
                if bets_placed >= max_bets:
                    continue

                try:
                    odds = await trial.get_current_odds()
                    if not odds.betting_open:
                        continue

                    selection = "home" if odds.home_probability > 0.5 else "away"
                    prob = max(odds.home_probability, odds.away_probability)

                    await trial.place_bet(
                        market="moneyline",
                        selection=selection,
                        amount=50.0,
                        reference_sequence=event.sequence,
                    )
                    bets_placed += 1
                    results["bets_placed"] = bets_placed
                    logger.info(
                        "[BETTING] Bet #%d: $%.0f on %s (prob=%.1f%%, seq=%d)",
                        bets_placed,
                        50.0,
                        selection,
                        prob * 100,
                        event.sequence,
                    )
                except Exception as e:
                    logger.debug("[BETTING] Bet skipped: %s", e)

        try:
            final_balance = await trial.get_balance()
            results["final_balance"] = final_balance.balance
            logger.info("[BETTING] Final balance: $%.2f", final_balance.balance)
        except Exception:
            pass

    return results


async def run_prediction_session(
    client: Any,
    gateway_url: str,
    api_key: str,
    max_predictions: int = 5,
) -> dict[str, Any]:
    """Run prediction session: listen to events and submit predictions."""
    results: dict[str, Any] = {
        "mode": "prediction",
        "predictions_made": 0,
        "events_seen": 0,
    }

    async with client.connect_trial(gateway_url=gateway_url, api_key=api_key) as trial:
        metadata = await trial.get_trial_metadata()
        logger.info(
            "[PREDICTION] Connected: %s vs %s (trial: %s)",
            metadata.away_team,
            metadata.home_team,
            trial.trial_id,
        )

        results["trial_id"] = trial.trial_id
        results["home_team"] = metadata.home_team
        results["away_team"] = metadata.away_team

        rules = await trial.get_rules()
        logger.info(
            "[PREDICTION] Contest: %d windows, selections=%s",
            rules.num_windows,
            rules.selections,
        )

        predictions_made = 0
        last_window = -1

        logger.info("[PREDICTION] Listening to events...")
        async for event in trial.events():
            results["events_seen"] += 1
            event_type = event.payload.get("event_type", "unknown")

            if "game_update" in event_type or "game_start" in event_type:
                if predictions_made >= max_predictions:
                    continue

                try:
                    info = await trial.get_event_info()
                    current_window = info.current_window

                    if current_window == last_window:
                        continue

                    # Simple strategy: predict home_win if home is leading
                    if info.home_score is not None and info.away_score is not None:
                        if info.home_score >= info.away_score:
                            selection = "home_win"
                        else:
                            selection = "away_win"
                    else:
                        selection = "home_win"

                    result = await trial.submit_prediction(selection=selection)
                    predictions_made += 1
                    last_window = current_window
                    results["predictions_made"] = predictions_made
                    logger.info(
                        "[PREDICTION] Prediction #%d: %s (window=%d, elapsed=%.1f%%)",
                        predictions_made,
                        selection,
                        result.window,
                        result.elapsed_ratio * 100,
                    )
                except Exception as e:
                    logger.debug("[PREDICTION] Prediction skipped: %s", e)

        try:
            preds = await trial.get_predictions()
            results["final_predictions"] = len(preds)
            logger.info("[PREDICTION] Total predictions: %d", len(preds))
        except Exception:
            pass

    return results


async def main(dashboard_url: str, api_key: str):
    """Main entry point: discover trials and run dual-mode sessions."""
    from dojozero_client import DojoClient

    print("=" * 70)
    print("DojoZero Dual-Mode Demo")
    print("External agent connecting to BOTH betting AND prediction trials")
    print("=" * 70)
    print()

    client = DojoClient(dashboard_url=dashboard_url)

    # Step 1: Discover trials
    logger.info("Discovering trials from %s ...", dashboard_url)
    try:
        gateways = await client.discover_trials()
    except Exception as e:
        logger.error("Discovery failed: %s", e)
        logger.error("Is dojo0 serve running at %s?", dashboard_url)
        return

    if not gateways:
        logger.error("No trials available. Is there a live game scheduled?")
        return

    print(f"Found {len(gateways)} trial(s):")
    for g in gateways:
        print(f"  - {g.trial_id}: {g.url}")
    print()

    # Step 2: Classify trials by mode (via /health, no registration needed)
    betting_gw = None
    prediction_gw = None

    async with httpx.AsyncClient(timeout=10.0) as http:
        for gw in gateways:
            try:
                resp = await http.get(f"{gw.url}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    mode = data.get("mode", "")
                    if mode == "classic_betting" and betting_gw is None:
                        betting_gw = gw
                        logger.info("Identified BETTING trial: %s", gw.trial_id)
                    elif mode == "prediction" and prediction_gw is None:
                        prediction_gw = gw
                        logger.info("Identified PREDICTION trial: %s", gw.trial_id)
            except Exception as e:
                logger.warning("Could not classify trial %s: %s", gw.trial_id, e)

    if betting_gw is None and prediction_gw is None:
        logger.error("No betting or prediction trials found.")
        return

    # Step 3: Run sessions
    tasks = []
    if betting_gw:
        print(f"[BETTING]    Trial: {betting_gw.trial_id}")
        if betting_gw.url:
            tasks.append(run_betting_session(client, betting_gw.url, api_key))
        else:
            print("[BETTING]    Trial has no URL - skipping")
    else:
        print("[BETTING]    No betting trial found - skipping")

    if prediction_gw:
        print(f"[PREDICTION] Trial: {prediction_gw.trial_id}")
        if prediction_gw.url:
            tasks.append(run_prediction_session(client, prediction_gw.url, api_key))
        else:
            print("[PREDICTION] Trial has no URL - skipping")
    else:
        print("[PREDICTION] No prediction trial found - skipping")

    print()
    print("-" * 70)
    print("Running dual-mode agent... (Ctrl+C to stop)")
    print("-" * 70)
    print()

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        results = []

    # Step 4: Print summary
    print()
    print("=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    for r in results:
        if isinstance(r, Exception):
            print(f"  ERROR: {r}")
        elif isinstance(r, dict):
            mode = r.get("mode", "unknown")
            print(f"\n  [{mode.upper()}] Trial: {r.get('trial_id', 'N/A')}")
            print(f"    Events seen: {r.get('events_seen', 0)}")
            if mode == "betting":
                print(f"    Bets placed: {r.get('bets_placed', 0)}")
                if "starting_balance" in r:
                    print(f"    Starting balance: ${r['starting_balance']:.2f}")
                if "final_balance" in r:
                    print(f"    Final balance: ${r['final_balance']:.2f}")
            elif mode == "prediction":
                print(f"    Predictions made: {r.get('predictions_made', 0)}")
                if "final_predictions" in r:
                    print(f"    Total predictions: {r['final_predictions']}")
    print()
    print("=" * 70)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Dual-mode demo: connect to both betting and prediction trials",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Prerequisites:
  1. Start the dashboard:  uv run dojo0 serve
  2. Register an agent:    uv run dojo0 agents add --id demo-dual-agent
  3. Run this demo:        python demo_dual_mode.py --api-key <key>
        """,
    )
    parser.add_argument(
        "--dashboard",
        default=os.environ.get("DOJOZERO_DASHBOARD_URL", "http://localhost:8000"),
        help="Dashboard URL (default: $DOJOZERO_DASHBOARD_URL or http://localhost:8000)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DOJOZERO_API_KEY", ""),
        help="Agent API key (default: $DOJOZERO_API_KEY)",
    )
    parser.add_argument(
        "--max-bets",
        type=int,
        default=3,
        help="Max bets to place (default: 3)",
    )
    parser.add_argument(
        "--max-predictions",
        type=int,
        default=5,
        help="Max predictions to submit (default: 5)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if not args.api_key:
        print("ERROR: --api-key is required (or set DOJOZERO_API_KEY env var)")
        print("  Register an agent first: uv run dojo0 agents add --id demo-dual-agent")
        sys.exit(1)

    try:
        asyncio.run(main(args.dashboard, args.api_key))
    except KeyboardInterrupt:
        print("\nInterrupted.")
