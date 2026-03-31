#!/usr/bin/env python3
"""Inspect trial directories for common issues.

Supports both server-side trial stores (with checkpoints/, results.json,
status.json) and client-side daemon state (~/.dojozero/trials/ with
state.json, bets.jsonl, events.jsonl).

Usage:
    python tools/check_trials.py <trials_dir> [options]

    # Check all trials for any issues
    python tools/check_trials.py /path/to/dojozero-store/trials

    # Only show trials with unsettled bets
    python tools/check_trials.py /path/to/trials --unsettled

    # Only show trials with specific status
    python tools/check_trials.py /path/to/trials --status completed
    python tools/check_trials.py /path/to/trials --status cancelled

    # Show summary of all trial results (leaderboard)
    python tools/check_trials.py /path/to/trials --results

    # Inspect a single trial in detail
    python tools/check_trials.py /path/to/trials/nba-game-401810946-xxx --detail

    # Output as JSON for scripting
    python tools/check_trials.py /path/to/trials --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def load_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON file, returning None on any error."""
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file, skipping bad lines."""
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


# ---------------------------------------------------------------------------
# Trial inspection
# ---------------------------------------------------------------------------


def find_broker_checkpoint(trial_dir: Path) -> dict[str, Any] | None:
    """Find the latest checkpoint containing broker state."""
    checkpoints_dir = trial_dir / "checkpoints"
    if not checkpoints_dir.exists():
        return None

    checkpoint_files = sorted(
        checkpoints_dir.glob("*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    for cp_file in checkpoint_files:
        data = load_json(cp_file)
        if data is None:
            continue
        broker = (data.get("actor_states") or {}).get("betting_broker")
        if broker and broker.get("bets"):
            return broker
    return None


def inspect_trial(trial_dir: Path) -> dict[str, Any]:
    """Build a comprehensive summary of a trial directory.

    Works with both server-side and client-side trial layouts.
    """
    info: dict[str, Any] = {"trial_id": trial_dir.name, "path": str(trial_dir)}

    # --- Server-side files ---
    results = load_json(trial_dir / "results.json")
    status = load_json(trial_dir / "status.json")
    state = load_json(trial_dir / "state.json")  # client daemon

    if results:
        info["status"] = results.get("status", "unknown")
        info["ended_at"] = results.get("endedAt", "")
        agents = results.get("results", [])
        info["agent_count"] = len(agents)
        info["agents_with_bets"] = sum(
            1 for a in agents if (a.get("totalBets") or 0) > 0
        )
        info["total_bets"] = sum(a.get("totalBets", 0) for a in agents)
        # Leaderboard
        info["agents"] = sorted(
            [
                {
                    "agent_id": a.get("agentId", ""),
                    "final_balance": float(a.get("finalBalance", 1000)),
                    "net_profit": float(a.get("netProfit", 0)),
                    "total_bets": a.get("totalBets", 0),
                    "win_rate": a.get("winRate", 0.0),
                    "roi": a.get("roi", 0.0),
                }
                for a in agents
            ],
            key=lambda a: a["final_balance"],
            reverse=True,
        )

    if status:
        info["phase"] = status.get("phase", "unknown")
        info["last_error"] = status.get("last_error")
        metadata = status.get("metadata", {})
        if metadata:
            info["game_id"] = metadata.get("espn_game_id", "")
            info["game_date"] = metadata.get("game_date", "")
            info["home_team"] = metadata.get("home_team_name", "")
            info["away_team"] = metadata.get("away_team_name", "")
            info["sport"] = metadata.get("sport_type", "")
        # Actor errors
        actor_errors = [
            {"actor_id": a["actor_id"], "error": a["last_error"]}
            for a in status.get("actors", [])
            if a.get("last_error")
        ]
        if actor_errors:
            info["actor_errors"] = actor_errors

    # --- Client-side daemon state ---
    if state:
        info["daemon_status"] = state.get("status", "unknown")
        info["daemon_balance"] = state.get("balance", 0.0)
        info["daemon_holdings"] = state.get("holdings", [])
        info["daemon_game_state"] = state.get("game_state", {})
        info["daemon_odds"] = state.get("current_odds", {})
        info["daemon_last_updated"] = state.get("last_updated", "")
        info["daemon_last_seq"] = state.get("last_event_sequence", 0)

    # --- Bets from JSONL (client-side) ---
    bets_jsonl = load_jsonl(trial_dir / "bets.jsonl")
    if bets_jsonl:
        info["client_bets"] = bets_jsonl

    # --- Broker checkpoint (server-side) ---
    broker = find_broker_checkpoint(trial_dir)
    if broker:
        bets = broker.get("bets", {})
        unsettled = [
            b
            for b in bets.values()
            if b.get("status") == "ACTIVE" and b.get("outcome") is None
        ]
        settled = [b for b in bets.values() if b not in unsettled]
        info["broker_total_bets"] = len(bets)
        info["broker_settled"] = len(settled)
        info["broker_unsettled"] = len(unsettled)
        if unsettled:
            info["unsettled_bets"] = [
                {
                    "agent_id": b.get("agent_id", ""),
                    "selection": b.get("selection", ""),
                    "amount": b.get("amount", 0),
                    "bet_type": b.get("bet_type", "unknown"),
                }
                for b in unsettled
            ]
        event = broker.get("event", {})
        if event:
            info["broker_event_status"] = event.get("status", "unknown")

    # --- Events from JSONL (client-side) ---
    events_file = trial_dir / "events.jsonl"
    if events_file.exists():
        info["has_events"] = True
        # Count without loading all into memory
        count = 0
        with open(events_file) as f:
            for _ in f:
                count += 1
        info["event_count"] = count

    # --- Persistence JSONL (server-side) ---
    persistence = (
        status.get("metadata", {}).get("persistence_file", "") if status else ""
    )
    if persistence:
        info["persistence_file"] = persistence

    return info


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def filter_unsettled(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in trials if t.get("broker_unsettled", 0) > 0]


def filter_status(trials: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    return [t for t in trials if t.get("status", "").lower() == status.lower()]


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def print_summary_table(trials: list[dict[str, Any]]) -> None:
    """Print a compact summary table."""
    if not trials:
        print("No trials found.")
        return

    # Header
    print(
        f"{'Trial ID':<45} {'Status':<12} {'Agents':<8} {'Bets':<6} {'Unsettled':<10} {'Ended':<20}"
    )
    print("-" * 105)

    for t in trials:
        trial_id = t.get("trial_id", "")[:44]
        status = t.get("status", t.get("daemon_status", "?"))[:11]
        agents = t.get("agent_count", "-")
        bets = t.get("total_bets", t.get("broker_total_bets", "-"))
        unsettled = t.get("broker_unsettled", "-")
        ended = (t.get("ended_at", "") or "")[:19]
        print(
            f"{trial_id:<45} {status:<12} {agents!s:<8} {bets!s:<6} {unsettled!s:<10} {ended:<20}"
        )

    # Summary
    total = len(trials)
    by_status: dict[str, int] = {}
    for t in trials:
        s = t.get("status", t.get("daemon_status", "unknown"))
        by_status[s] = by_status.get(s, 0) + 1
    unsettled_count = sum(1 for t in trials if t.get("broker_unsettled", 0) > 0)

    print("-" * 105)
    status_parts = ", ".join(f"{v} {k}" for k, v in sorted(by_status.items()))
    print(f"Total: {total} trials ({status_parts})")
    if unsettled_count:
        print(f"Unsettled bets: {unsettled_count} trial(s)")


def print_results_table(trials: list[dict[str, Any]]) -> None:
    """Print agent results across trials."""
    for t in trials:
        agents = t.get("agents", [])
        if not agents:
            continue

        game_label = ""
        if t.get("home_team") and t.get("away_team"):
            game_label = f" ({t['home_team']} vs {t['away_team']})"

        print(f"\n{'=' * 80}")
        print(f"Trial: {t['trial_id']}{game_label}")
        print(
            f"Status: {t.get('status', '?')}  |  Ended: {(t.get('ended_at', '') or '')[:19]}"
        )
        print(f"{'=' * 80}")
        print(
            f"  {'Agent':<45} {'Balance':>9} {'Profit':>9} {'Bets':>5} {'Win%':>6} {'ROI':>7}"
        )
        print(f"  {'-' * 45} {'-' * 9} {'-' * 9} {'-' * 5} {'-' * 6} {'-' * 7}")

        for a in agents:
            agent_id = a["agent_id"][:44]
            bal = f"${a['final_balance']:.0f}"
            profit = f"${a['net_profit']:+.0f}"
            bets = str(a["total_bets"])
            win = f"{a['win_rate'] * 100:.0f}%" if a["total_bets"] > 0 else "-"
            roi = f"{a['roi'] * 100:.0f}%" if a["total_bets"] > 0 else "-"
            print(f"  {agent_id:<45} {bal:>9} {profit:>9} {bets:>5} {win:>6} {roi:>7}")


def print_detail(trial: dict[str, Any]) -> None:
    """Print detailed view of a single trial."""
    print(f"{'=' * 70}")
    print(f"Trial: {trial['trial_id']}")
    print(f"{'=' * 70}")

    # Game info
    if trial.get("home_team"):
        print(f"\nGame: {trial.get('home_team', '?')} vs {trial.get('away_team', '?')}")
        print(
            f"  Sport: {trial.get('sport', '?')}  |  Date: {trial.get('game_date', '?')}"
        )
        print(f"  ESPN ID: {trial.get('game_id', '?')}")

    # Status
    print(f"\nStatus: {trial.get('status', trial.get('daemon_status', '?'))}")
    print(f"Phase: {trial.get('phase', '?')}")
    if trial.get("ended_at"):
        print(f"Ended: {trial['ended_at']}")
    if trial.get("last_error"):
        print(f"Error: {trial['last_error']}")

    # Daemon state (client-side)
    if trial.get("daemon_balance") is not None and "daemon_status" in trial:
        print("\nDaemon state:")
        print(f"  Balance: ${trial['daemon_balance']:.2f}")
        print(f"  Holdings: {trial.get('daemon_holdings', [])}")
        print(f"  Game: {trial.get('daemon_game_state', {})}")
        print(f"  Odds: {trial.get('daemon_odds', {})}")
        print(f"  Last seq: {trial.get('daemon_last_seq', 0)}")
        print(f"  Updated: {trial.get('daemon_last_updated', '?')}")

    # Bets summary
    if trial.get("broker_total_bets"):
        print(
            f"\nBroker bets: {trial['broker_settled']} settled, {trial['broker_unsettled']} unsettled / {trial['broker_total_bets']} total"
        )
        if trial.get("broker_event_status"):
            print(f"  Event status: {trial['broker_event_status']}")
        if trial.get("unsettled_bets"):
            print("  Unsettled:")
            for b in trial["unsettled_bets"]:
                print(
                    f"    - {b['agent_id']}: {b['bet_type']} {b['selection']} ${b['amount']}"
                )

    if trial.get("client_bets"):
        print(f"\nClient bets ({len(trial['client_bets'])}):")
        for b in trial["client_bets"]:
            status_str = b.get("status", "?")
            print(
                f"  - {b.get('market', '?')} {b.get('selection', '?')} ${b.get('amount', 0)} [{status_str}] @ {b.get('placed_at', '?')}"
            )

    # Agent results
    if trial.get("agents"):
        active = [a for a in trial["agents"] if a["total_bets"] > 0]
        inactive = [a for a in trial["agents"] if a["total_bets"] == 0]
        if active:
            print(f"\nAgent results ({len(active)} active, {len(inactive)} idle):")
            print(
                f"  {'Agent':<40} {'Balance':>9} {'Profit':>9} {'Bets':>5} {'Win%':>6}"
            )
            print(f"  {'-' * 40} {'-' * 9} {'-' * 9} {'-' * 5} {'-' * 6}")
            for a in active:
                agent_id = a["agent_id"][:39]
                bal = f"${a['final_balance']:.0f}"
                profit = f"${a['net_profit']:+.0f}"
                bets = str(a["total_bets"])
                win = f"{a['win_rate'] * 100:.0f}%"
                print(f"  {agent_id:<40} {bal:>9} {profit:>9} {bets:>5} {win:>6}")

    # Actor errors
    if trial.get("actor_errors"):
        print("\nActor errors:")
        for e in trial["actor_errors"]:
            print(f"  - {e['actor_id']}: {e['error']}")

    if trial.get("event_count"):
        print(f"\nEvents: {trial['event_count']} in events.jsonl")
    if trial.get("persistence_file"):
        print(f"Persistence: {trial['persistence_file']}")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect trial directories for issues and results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s /path/to/trials                     # summary table
  %(prog)s /path/to/trials --unsettled         # only unsettled bets
  %(prog)s /path/to/trials --status cancelled  # only cancelled trials
  %(prog)s /path/to/trials --results           # agent leaderboards
  %(prog)s /path/to/trials/trial-id --detail   # single trial deep dive
  %(prog)s /path/to/trials --json              # machine-readable output
""",
    )
    parser.add_argument("path", help="Trials directory or single trial path")
    parser.add_argument(
        "--unsettled", action="store_true", help="Only show trials with unsettled bets"
    )
    parser.add_argument(
        "--status", help="Filter by trial status (completed, cancelled, etc.)"
    )
    parser.add_argument(
        "--results", action="store_true", help="Show agent results/leaderboard"
    )
    parser.add_argument(
        "--detail", action="store_true", help="Detailed view (single trial or all)"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    target = Path(args.path)

    if not target.exists():
        print(f"Error: {target} does not exist", file=sys.stderr)
        return 1

    # Determine if target is a single trial or a directory of trials
    is_single = (
        (target / "results.json").exists()
        or (target / "state.json").exists()
        or (target / "status.json").exists()
    )

    if is_single:
        trial = inspect_trial(target)
        if args.json:
            print(json.dumps(trial, indent=2))
        else:
            print_detail(trial)
        return 0

    # Multiple trials
    if not target.is_dir():
        print(f"Error: {target} is not a directory", file=sys.stderr)
        return 1

    trial_dirs = sorted(
        [d for d in target.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )

    trials = [inspect_trial(d) for d in trial_dirs]

    # Apply filters
    if args.unsettled:
        trials = filter_unsettled(trials)
    if args.status:
        trials = filter_status(trials, args.status)

    if args.json:
        print(json.dumps(trials, indent=2))
    elif args.detail:
        for t in trials:
            print_detail(t)
    elif args.results:
        print_results_table(trials)
    else:
        print_summary_table(trials)

    return 0


if __name__ == "__main__":
    sys.exit(main())
