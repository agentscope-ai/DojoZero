"""CLI for dojozero-agent daemon.

Usage:
    dojozero-agent start <trial-id> [options]
    dojozero-agent stop
    dojozero-agent status
    dojozero-agent logs [-f]
    dojozero-agent bet <amount> <market> <selection>
    dojozero-agent notifications
"""

from __future__ import annotations

import os

# Ensure localhost connections bypass proxy
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from dojozero_client._config import CONFIG_DIR
from dojozero_client._daemon import (
    Daemon,
    DaemonConfig,
    get_daemon_status,
    is_daemon_running,
    stop_daemon,
)

logger = logging.getLogger(__name__)


def cmd_start(args: argparse.Namespace) -> int:
    """Start the daemon for a trial."""
    state_dir = Path(args.state_dir) if args.state_dir else CONFIG_DIR

    if is_daemon_running(state_dir):
        print("Daemon already running. Use 'stop' first.", file=sys.stderr)
        return 1

    api_key = args.api_key or os.environ.get("DOJOZERO_AGENT_API_KEY", "")
    if not api_key:
        print(
            "Error: API key required. Use --api-key or set DOJOZERO_AGENT_API_KEY.",
            file=sys.stderr,
        )
        return 1

    config = DaemonConfig(
        trial_id=args.trial_id,
        gateway_url=args.gateway
        or os.environ.get("DOJOZERO_GATEWAY_URL", "http://localhost:8080"),
        api_key=api_key,
        state_dir=state_dir,
        strategy=args.strategy,
        auto_bet=args.auto_bet,
        notify=args.notify.split(",") if args.notify else ["file"],
        filters=args.filters.split(",") if args.filters else ["event.*", "odds.*"],
    )

    if args.background:
        # Start as background process
        # Note: --state-dir is a global arg (before subcommand)
        cmd = [
            sys.executable,
            "-m",
            "dojozero_client._cli",
            "--state-dir",
            str(state_dir),
            "start",
            args.trial_id,
            "--gateway",
            config.gateway_url,
        ]
        if config.api_key:
            cmd.extend(["--api-key", config.api_key])
        if config.strategy:
            cmd.extend(["--strategy", config.strategy])
        if config.auto_bet:
            cmd.append("--auto-bet")
        # Don't pass --background to avoid infinite recursion

        state_dir.mkdir(parents=True, exist_ok=True)
        log_file = state_dir / "daemon.log"

        # Inherit environment and ensure proxy bypass for localhost
        env = os.environ.copy()
        env.setdefault("NO_PROXY", "localhost,127.0.0.1")

        with open(log_file, "a") as f:
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        print(f"Started daemon for {args.trial_id} (background)")
        print(f"Logs: {log_file}")
        return 0

    # Run in foreground
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    daemon = Daemon(config)
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        print("\nStopping daemon...")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Stop the running daemon."""
    state_dir = Path(args.state_dir) if args.state_dir else CONFIG_DIR

    if not is_daemon_running(state_dir):
        print("No daemon running")
        return 1

    if stop_daemon(state_dir):
        print("Daemon stopped")
        return 0
    else:
        print("Failed to stop daemon", file=sys.stderr)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show current daemon status."""
    state_dir = Path(args.state_dir) if args.state_dir else CONFIG_DIR
    state = get_daemon_status(state_dir)

    if not state:
        print("No active trial. Use 'start <trial-id>' first.")
        return 1

    running = is_daemon_running(state_dir)
    game_state = state.get("game_state", {})
    odds = state.get("current_odds", {})

    print(f"Trial: {state.get('trial_id', 'unknown')}")
    print(f"Agent: {state.get('agent_id', 'unknown')}")
    print(
        f"Status: {state.get('status', 'unknown')} {'(running)' if running else '(stopped)'}"
    )

    if game_state:
        home = game_state.get("home_score", "?")
        away = game_state.get("away_score", "?")
        period = game_state.get("period", game_state.get("quarter", "?"))
        clock = game_state.get("clock", game_state.get("time", ""))
        print(f"Score: {away}-{home} (Q{period} {clock})")

    if odds:
        home_prob = odds.get("home_probability", 0)
        away_prob = odds.get("away_probability", 0)
        print(f"Odds: Home {home_prob:.0%}, Away {away_prob:.0%}")

    print(f"Balance: ${state.get('balance', 0):.2f}")
    print(f"Last Update: {state.get('last_updated', 'never')}")

    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    """Show daemon logs."""
    state_dir = Path(args.state_dir) if args.state_dir else CONFIG_DIR
    log_file = state_dir / "daemon.log"

    if not log_file.exists():
        print("No logs available")
        return 1

    if args.follow:
        # Use tail -f for following
        try:
            subprocess.run(["tail", "-f", str(log_file)])
        except KeyboardInterrupt:
            pass
    else:
        # Show last 50 lines
        lines = log_file.read_text().strip().split("\n")
        for line in lines[-50:]:
            print(line)

    return 0


def cmd_bet(args: argparse.Namespace) -> int:
    """Place a bet via the REST API."""
    import httpx

    state_dir = Path(args.state_dir) if args.state_dir else CONFIG_DIR
    state = get_daemon_status(state_dir)

    if not state:
        print("No active trial. Use 'start <trial-id>' first.", file=sys.stderr)
        return 1

    gateway_url = os.environ.get("DOJOZERO_GATEWAY_URL", "http://localhost:8000")
    agent_id = state.get("agent_id", "")

    try:
        resp = httpx.post(
            f"{gateway_url}/api/v1/bets",
            headers={"X-Agent-ID": agent_id},
            json={
                "market": args.market,
                "selection": args.selection,
                "amount": args.amount,
            },
            timeout=10.0,
        )

        if resp.status_code != 200:
            try:
                error = resp.json().get("error", {})
                print(f"Error: {error.get('message', resp.text)}", file=sys.stderr)
            except Exception:
                print(f"Error: {resp.status_code} - {resp.text}", file=sys.stderr)
            return 1

        data = resp.json()
        print(f"Bet placed: ${args.amount} on {args.selection} ({args.market})")
        print(f"Bet ID: {data.get('betId')}")
        return 0

    except httpx.ConnectError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return 1


def cmd_notifications(args: argparse.Namespace) -> int:
    """Show recent notifications."""
    state_dir = Path(args.state_dir) if args.state_dir else CONFIG_DIR
    notif_file = state_dir / "notifications.jsonl"

    if not notif_file.exists():
        print("No notifications")
        return 0

    lines = notif_file.read_text().strip().split("\n")
    count = args.count if hasattr(args, "count") and args.count else 10

    for line in lines[-count:]:
        if not line:
            continue
        try:
            notif = json.loads(line)
            ts = notif.get("ts", "")[:19]  # Trim to datetime
            msg = notif.get("message", "")
            ntype = notif.get("type", "")
            print(f"[{ts}] ({ntype}) {msg}")
        except json.JSONDecodeError:
            continue

    return 0


def cmd_events(args: argparse.Namespace) -> int:
    """Show recent events."""
    state_dir = Path(args.state_dir) if args.state_dir else CONFIG_DIR
    events_file = state_dir / "events.jsonl"

    if not events_file.exists():
        print("No events")
        return 0

    lines = events_file.read_text().strip().split("\n")
    count = args.count if hasattr(args, "count") and args.count else 20

    for line in lines[-count:]:
        if not line:
            continue
        try:
            event = json.loads(line)
            seq = event.get("sequence", "?")
            etype = event.get("type", "unknown")
            ts = event.get("timestamp", "")[:19]
            print(f"[{seq}] {ts} {etype}")
        except json.JSONDecodeError:
            continue

    return 0


def cmd_bets(args: argparse.Namespace) -> int:
    """Show bet history."""
    state_dir = Path(args.state_dir) if args.state_dir else CONFIG_DIR
    bets_file = state_dir / "bets.jsonl"

    if not bets_file.exists():
        print("No bets")
        return 0

    lines = bets_file.read_text().strip().split("\n")
    count = args.count if hasattr(args, "count") and args.count else 20

    for line in lines[-count:]:
        if not line:
            continue
        try:
            bet = json.loads(line)
            bet_id = bet.get("bet_id", "?")[:8]
            market = bet.get("market", "?")
            selection = bet.get("selection", "?")
            amount = bet.get("amount", 0)
            status = bet.get("status", "?")
            print(f"[{bet_id}] ${amount:.2f} on {selection} ({market}) - {status}")
        except json.JSONDecodeError:
            continue

    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover available trials from dashboard."""
    from dojozero_client._client import DojoClient

    dashboard_urls = None
    if args.dashboard:
        dashboard_urls = [args.dashboard]

    client = DojoClient(dashboard_urls=dashboard_urls)

    try:
        gateways = asyncio.run(client.discover_trials())
    except Exception as e:
        print(f"Discovery failed: {e}", file=sys.stderr)
        return 1

    if not gateways:
        print("No trials available")
        return 0

    print("Available trials:")
    for g in gateways:
        print(f"  {g.trial_id}: {g.url or g.endpoint}")

    return 0


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser."""
    parser = argparse.ArgumentParser(
        prog="dojozero-agent",
        description="DojoZero agent daemon for persistent trial connections",
    )
    parser.add_argument(
        "--state-dir",
        help=f"State directory (default: {CONFIG_DIR})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = subparsers.add_parser("start", help="Start daemon for a trial")
    p_start.add_argument("trial_id", help="Trial ID to connect to")
    p_start.add_argument(
        "--gateway",
        "-g",
        help="Gateway URL (default: $DOJOZERO_GATEWAY_URL or localhost:8080)",
    )
    p_start.add_argument(
        "--api-key",
        help="API key for authentication (required, or set $DOJOZERO_AGENT_API_KEY)",
    )
    p_start.add_argument(
        "--strategy",
        "-s",
        help="Strategy module path (e.g., dojozero_client._strategy.conservative)",
    )
    p_start.add_argument(
        "--auto-bet",
        action="store_true",
        help="Enable autonomous betting with strategy",
    )
    p_start.add_argument(
        "--notify",
        default="file",
        help="Notification methods (comma-separated, default: file)",
    )
    p_start.add_argument(
        "--filters",
        default="event.*,odds.*",
        help="Event type filters (comma-separated)",
    )
    p_start.add_argument(
        "--background",
        "-b",
        action="store_true",
        help="Run in background",
    )
    p_start.set_defaults(func=cmd_start)

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop the daemon")
    p_stop.set_defaults(func=cmd_stop)

    # status
    p_status = subparsers.add_parser("status", help="Show daemon status")
    p_status.set_defaults(func=cmd_status)

    # logs
    p_logs = subparsers.add_parser("logs", help="Show daemon logs")
    p_logs.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log output",
    )
    p_logs.set_defaults(func=cmd_logs)

    # bet
    p_bet = subparsers.add_parser("bet", help="Place a bet")
    p_bet.add_argument("amount", type=float, help="Bet amount")
    p_bet.add_argument(
        "market",
        choices=["moneyline", "spread", "total"],
        help="Market type",
    )
    p_bet.add_argument("selection", help="Selection (e.g., home, away, over, under)")
    p_bet.set_defaults(func=cmd_bet)

    # notifications
    p_notif = subparsers.add_parser("notifications", help="Show notifications")
    p_notif.add_argument("-n", "--count", type=int, default=10, help="Number to show")
    p_notif.set_defaults(func=cmd_notifications)

    # events
    p_events = subparsers.add_parser("events", help="Show event log")
    p_events.add_argument("-n", "--count", type=int, default=20, help="Number to show")
    p_events.set_defaults(func=cmd_events)

    # bets
    p_bets = subparsers.add_parser("bets", help="Show bet history")
    p_bets.add_argument("-n", "--count", type=int, default=20, help="Number to show")
    p_bets.set_defaults(func=cmd_bets)

    # discover
    p_discover = subparsers.add_parser("discover", help="Discover available trials")
    p_discover.add_argument(
        "--dashboard",
        "-d",
        help="Dashboard URL (default: $DOJOZERO_DASHBOARD_URL)",
    )
    p_discover.set_defaults(func=cmd_discover)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
