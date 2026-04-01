"""CLI for dojozero-agent daemon.

Usage:
    dojozero-agent start <trial-id> [options]
    dojozero-agent stop
    dojozero-agent status
    dojozero-agent logs [-f]
    dojozero-agent bet <amount> <market> <selection>
    dojozero-agent events [-n N] [--format {summary,json}] [--type TYPE]
    dojozero-agent leaderboard [trial-id] [--format {table,json}]
"""

from __future__ import annotations

import os

# Ensure localhost connections bypass proxy
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from dojozero_client._config import (
    CONFIG_DIR,
    SOCKET_PATH,
    has_config,
    load_config,
    save_config,
)
from dojozero_client._credentials import (
    get_default_profile,
    get_profile_dir,
    has_api_key,
    list_profiles,
    load_api_key,
    save_api_key,
    set_default_profile,
)
from dojozero_client._daemon import (
    UnifiedDaemon,
    get_daemon_status,
    is_daemon_running,
    stop_daemon,
    _trial_state_dir,
)
from dojozero_client._rpc import RPCClient, RPCError

logger = logging.getLogger(__name__)


def _get_state_dir(args: argparse.Namespace) -> Path:
    """Get state directory from args.

    Priority:
    1. Explicit --state-dir override
    2. Computed from trial_id: ~/.dojozero/trials/{trial_id}/
    3. Fallback: ~/.dojozero/
    """
    if args.state_dir:
        return Path(args.state_dir)
    if hasattr(args, "trial_id") and args.trial_id:
        return _trial_state_dir(args.trial_id)
    return CONFIG_DIR


def _get_profile(args: argparse.Namespace) -> str | None:
    """Get profile from args or environment."""
    return getattr(args, "profile", None) or os.environ.get("DOJOZERO_PROFILE")


def _ensure_daemon_running(profile: str | None) -> bool:
    """Ensure the unified daemon is running, starting it if needed.

    Returns:
        True if daemon is running (was already running or just started).
    """
    if is_daemon_running():
        return True

    if not has_api_key(profile):
        profile_hint = f" --profile {profile}" if profile else ""
        print(
            f"Error: No API key configured. Run 'dojozero-agent config{profile_hint} --api-key <key>' first.",
            file=sys.stderr,
        )
        return False

    profile_dir = get_profile_dir(profile)
    profile_dir.mkdir(parents=True, exist_ok=True)
    log_file = profile_dir / "daemon.log"

    env = os.environ.copy()
    env.setdefault("NO_PROXY", "localhost,127.0.0.1")

    cmd = [sys.executable, "-m", "dojozero_client._cli"]
    if profile:
        cmd.extend(["--profile", profile])
    cmd.append("daemon")

    with open(log_file, "a") as f:
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
        )

    # Wait briefly for daemon to start
    import time

    for _ in range(10):
        time.sleep(0.3)
        if is_daemon_running():
            return True
    print("Error: Daemon failed to start. Check logs.", file=sys.stderr)
    return False


def cmd_start(args: argparse.Namespace) -> int:
    """Start (join) a trial. Auto-starts daemon if needed."""
    profile = _get_profile(args)

    filters = args.filters.split(",") if args.filters else ["event.*", "odds.*"]

    if args.background:
        # Ensure daemon is running, then join via RPC
        if not _ensure_daemon_running(profile):
            return 1

        client = RPCClient(SOCKET_PATH)
        try:
            result = client.call_sync(
                "join",
                trial_id=args.trial_id,
                filters=filters,
            )
            status = result.get("status", "joined")
            agent_id = result.get("agent_id", "")
            if status == "already_joined":
                print(f"Already joined trial {args.trial_id} as {agent_id}")
            else:
                print(f"Joined trial {args.trial_id} as {agent_id}")
            return 0
        except RPCError as e:
            print(f"Error: {e.message}", file=sys.stderr)
            return 1
        except ConnectionError as e:
            print(f"Cannot connect to daemon: {e}", file=sys.stderr)
            return 1

    # Foreground mode: run daemon in-process with a single trial
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    async def _run_foreground() -> None:
        daemon = UnifiedDaemon()
        # Start daemon, then auto-join the trial
        daemon._stop_event = asyncio.Event()
        daemon._api_key = load_api_key(profile=profile)
        if not daemon._api_key:
            raise RuntimeError("No API key configured")
        daemon._write_pid()
        daemon._setup_signals()
        daemon._running = True
        await daemon._rpc.start()
        try:
            await daemon._handle_join(
                trial_id=args.trial_id,
                filters=filters,
            )
            logger.info("Joined trial %s in foreground mode", args.trial_id)
            await daemon._stop_event.wait()
        finally:
            await daemon.stop()

    try:
        asyncio.run(_run_foreground())
    except KeyboardInterrupt:
        print("\nStopping daemon...")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Stop a trial or the entire daemon."""
    trial_id = getattr(args, "trial_id", None)

    if not is_daemon_running():
        print("Daemon not running")
        return 1

    if trial_id:
        # Disconnect from a specific trial (keep daemon running)
        client = RPCClient(SOCKET_PATH)
        try:
            client.call_sync("leave", trial_id=trial_id)
            print(f"Disconnected from trial {trial_id}")
            return 0
        except RPCError as e:
            print(f"Error: {e.message}", file=sys.stderr)
            return 1
    else:
        # Stop the entire daemon
        if stop_daemon():
            print("Daemon stopped")
            return 0
        else:
            print("Failed to stop daemon", file=sys.stderr)
            return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show current trial status."""
    trial_id = getattr(args, "trial_id", None)
    daemon_running = is_daemon_running()

    # Try RPC first for live data
    if daemon_running:
        client = RPCClient(SOCKET_PATH)
        try:
            state = client.call_sync("status", trial_id=trial_id)
            _print_status(state, daemon_running=True)
            # Show bet history from disk
            tid = state.get("trial_id", trial_id or "")
            if tid:
                _print_bet_history(_trial_state_dir(tid))
            return 0
        except RPCError as e:
            if e.code == "NO_TRIALS":
                print("No trials connected. Use 'start <trial-id>' first.")
                return 1
            print(f"Error: {e.message}", file=sys.stderr)
            return 1

    # Fallback to disk state
    state_dir = _get_state_dir(args)
    state = get_daemon_status(trial_id=trial_id, state_dir=state_dir)
    if not state:
        print(
            f"No active trial{f' for {trial_id}' if trial_id else ''}. Use 'start <trial-id>' first."
        )
        return 1

    _print_status(state, daemon_running=False)
    _print_bet_history(state_dir)
    return 0


def _print_status(state: dict[str, Any], daemon_running: bool) -> None:
    """Print formatted status output."""
    game_state = state.get("game_state", {})
    odds = state.get("current_odds", {})
    home_team = state.get("home_team", "")
    away_team = state.get("away_team", "")
    home_tri = state.get("home_team_tricode", "")
    away_tri = state.get("away_team_tricode", "")

    print(f"Trial: {state.get('trial_id', 'unknown')}")
    if home_team and away_team:
        sport = state.get("sport_type", "")
        game_time = state.get("game_time", "")
        home_display = f"{home_team} ({home_tri})" if home_tri else home_team
        away_display = f"{away_team} ({away_tri})" if away_tri else away_team
        matchup = f"Game: {away_display} @ {home_display}"
        if sport:
            matchup += f" [{sport}]"
        if game_time:
            matchup += f" - {game_time}"
        print(matchup)
    print(f"Agent: {state.get('agent_id', 'unknown')}")
    status_label = state.get("status", "unknown")
    if daemon_running:
        print(f"Status: {status_label} (daemon running)")
    else:
        print(f"Status: {status_label} (daemon not running)")

    # Use tricodes for compact score/odds, fall back to full name or "Home"/"Away"
    home_label = home_tri or home_team or "Home"
    away_label = away_tri or away_team or "Away"

    if game_state:
        home_score = game_state.get("home_score", "?")
        away_score = game_state.get("away_score", "?")
        period = game_state.get("period", game_state.get("quarter", "?"))
        clock = game_state.get("clock", game_state.get("time", ""))
        print(
            f"Score: {home_label} {home_score} - {away_label} {away_score} (Q{period} {clock})"
        )

    if odds:
        home_prob = odds.get("home_probability", 0)
        away_prob = odds.get("away_probability", 0)
        print(f"Odds: {home_label} {home_prob:.0%}, {away_label} {away_prob:.0%}")

    print(f"Balance: ${state.get('balance', 0):.2f}")
    print(f"Last Update: {state.get('last_updated', 'never')}")


def _print_bet_history(state_dir: Path) -> None:
    """Print bet history from bets.jsonl."""
    bets_file = state_dir / "bets.jsonl"
    if not bets_file.exists():
        return

    lines = bets_file.read_text().strip().split("\n")
    bets = []
    for line in lines:
        if line:
            try:
                bets.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if bets:
        total_wagered = sum(b.get("amount", 0) for b in bets)
        print(f"\nBets ({len(bets)}, ${total_wagered:.2f} wagered):")
        for b in bets:
            amt = b.get("amount", 0)
            market = b.get("market", "?")
            selection = b.get("selection", "?")
            prob = b.get("probability", 0)
            ts = b.get("placed_at", "")[:16]
            prob_str = f" @ {prob:.0%}" if prob else ""
            print(f"  ${amt:.0f} on {selection} ({market}){prob_str} - {ts}")


def cmd_logs(args: argparse.Namespace) -> int:
    """Show daemon logs."""
    state_dir = _get_state_dir(args)
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
    """Place a bet via daemon RPC."""
    trial_id = getattr(args, "trial_id", None)

    spread_value: float | None = getattr(args, "spread_value", None)
    total_value: float | None = getattr(args, "total_value", None)

    if args.market == "spread" and spread_value is None:
        print("Error: --spread-value required for spread bets", file=sys.stderr)
        return 1
    if args.market == "total" and total_value is None:
        print("Error: --total-value required for total bets", file=sys.stderr)
        return 1

    if not is_daemon_running():
        print("Daemon not running. Use 'start <trial-id>' first.", file=sys.stderr)
        return 1

    client = RPCClient(SOCKET_PATH)
    try:
        result = client.call_sync(
            "bet",
            trial_id=trial_id,
            amount=args.amount,
            market=args.market,
            selection=args.selection,
            spread_value=spread_value,
            total_value=total_value,
        )
        print(f"Bet placed: ${args.amount} on {args.selection} ({args.market})")
        print(f"Bet ID: {result.get('bet_id')}")
        return 0
    except RPCError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        return 1


def _format_event_summary(payload: dict[str, Any], home: str, away: str) -> str:
    """Format an event payload as a human-readable summary line."""
    event_type = payload.get("event_type", "")

    if event_type == "event.nba_game_update":
        period = payload.get("period", "?")
        clock = payload.get("game_clock", "")
        h_score = payload.get("home_score", "?")
        a_score = payload.get("away_score", "?")
        quarter = f"Q{period}" if isinstance(period, int) else str(period)
        time_str = f"{quarter} {clock}" if clock else quarter
        return f"{time_str} {home} {h_score}-{a_score} {away}"

    if event_type == "event.nba_play":
        period = payload.get("period", "?")
        clock = payload.get("clock", "")
        h_score = payload.get("home_score", "?")
        a_score = payload.get("away_score", "?")
        desc = payload.get("description", "")
        quarter = f"Q{period}" if isinstance(period, int) else str(period)
        time_str = f"{quarter} {clock}" if clock else quarter
        score = f"{home} {h_score}-{a_score} {away}"
        return f"{time_str} {score} | {desc}" if desc else f"{time_str} {score}"

    if event_type == "event.odds_update":
        odds = payload.get("odds", {})
        parts: list[str] = []
        ml = odds.get("moneyline", {})
        if ml:
            hp = ml.get("home_probability", 0)
            parts.append(f"ML home {hp:.1%}")
        for t in odds.get("totals", [])[:1]:
            total_val = t.get("total", "?")
            under_p = t.get("under_probability", 0)
            parts.append(f"total {total_val} under {under_p:.1%}")
        for s in odds.get("spreads", [])[:1]:
            spread_val = s.get("spread", "?")
            away_p = s.get("away_probability", 0)
            parts.append(f"spread {spread_val:+g} away cover {away_p:.1%}")
        return " | ".join(parts) if parts else "odds update"

    if event_type == "event.pregame_stats":
        return "pregame stats"

    if event_type == "event.game_result":
        winner = payload.get("winner", "?")
        h_score = payload.get("home_score", "?")
        a_score = payload.get("away_score", "?")
        h_name = payload.get("home_team_name", home)
        a_name = payload.get("away_team_name", away)
        return f"FINAL {h_name} {h_score}-{a_score} {a_name} (winner: {winner})"

    return event_type


def cmd_events(args: argparse.Namespace) -> int:
    """Show recent events."""
    state_dir = _get_state_dir(args)
    events_file = state_dir / "events.jsonl"

    if not events_file.exists():
        print("No events")
        return 0

    output_format: str = getattr(args, "format", "summary")
    type_filter: set[str] | None = None
    if raw_types := getattr(args, "type", None):
        type_filter = {
            f"event.{t}" if not t.startswith("event.") else t
            for t in raw_types.split(",")
        }

    lines = events_file.read_text().strip().split("\n")
    count = args.count if hasattr(args, "count") and args.count else 20

    # First pass: discover team tricodes from odds/result events
    home_tri = "HOME"
    away_tri = "AWAY"
    for line in lines:
        if not line:
            continue
        try:
            payload = json.loads(line).get("payload", {})
            # odds_update has top-level tricodes
            if payload.get("home_tricode"):
                home_tri = payload["home_tricode"]
                away_tri = payload.get("away_tricode", "AWAY")
                break
            # nba_game_update sometimes has tricodes in team_stats
            h_stats = payload.get("home_team_stats", {})
            if h_stats.get("team_tricode"):
                home_tri = h_stats["team_tricode"]
                away_tri = (
                    payload.get("away_team_stats", {}).get("team_tricode") or "AWAY"
                )
                break
            # game_result has full team names
            if payload.get("home_team_name"):
                home_tri = payload["home_team_name"]
                away_tri = payload.get("away_team_name", "AWAY")
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    shown = 0
    # Iterate from end to collect last `count` matching events
    matching: list[str] = []
    for line in reversed(lines):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload", {})
        event_type = payload.get("event_type", "")
        if type_filter and event_type not in type_filter:
            continue
        seq = event.get("sequence", "?")
        if output_format == "json":
            matching.append(json.dumps(event))
        else:
            summary = _format_event_summary(payload, home_tri, away_tri)
            ts = event.get("timestamp", "")[:19]
            matching.append(f"[{seq}] {ts} {summary}")
        shown += 1
        if shown >= count:
            break

    for line in reversed(matching):
        print(line)

    return 0


def cmd_bets(args: argparse.Namespace) -> int:
    """Show bet history."""
    state_dir = _get_state_dir(args)
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


def cmd_list(_: argparse.Namespace) -> int:
    """List all connected trials."""
    if not is_daemon_running():
        print("Daemon not running")
        return 0

    client = RPCClient(SOCKET_PATH)
    try:
        result = client.call_sync("list")
        trials = result.get("trials", {})

        if not trials:
            print("No trials connected")
            return 0

        print(f"Connected trials ({len(trials)}):")
        for trial_id, info in trials.items():
            status = "connected" if info.get("connected") else "disconnected"
            balance = info.get("balance", 0)
            print(f"  {trial_id}: {status}, balance=${balance:.2f}")
        return 0
    except RPCError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        return 1


def cmd_leaderboard(args: argparse.Namespace) -> int:
    """Show trial leaderboard."""
    import httpx

    trial_id = getattr(args, "trial_id", None)

    # Resolve trial_id: explicit arg > daemon's active trial > error
    if not trial_id and is_daemon_running():
        client = RPCClient(SOCKET_PATH)
        try:
            result = client.call_sync("status")
            trial_id = result.get("trial_id")
        except (RPCError, ConnectionError):
            pass

    if not trial_id:
        print(
            "Trial ID required. Use 'leaderboard <trial-id>' or join a trial first.",
            file=sys.stderr,
        )
        return 1

    gateway_url = load_config().get_gateway_url(trial_id)

    try:
        resp = httpx.get(f"{gateway_url}/leaderboard", timeout=10.0)
        if resp.status_code != 200:
            print(f"Error: {resp.status_code} - {resp.text}", file=sys.stderr)
            return 1

        data = resp.json()
        board = data.get("leaderboard", [])

        if not board:
            print("No agents registered")
            return 0

        output_format = getattr(args, "format", "table")
        if output_format == "json":
            print(json.dumps(data, indent=2))
            return 0

        # Table format
        print(f"Leaderboard for {data.get('trial_id', trial_id)}")
        print(
            f"  {data.get('total_agents', 0)} agents "
            f"({data.get('external_agents', 0)} external, "
            f"{data.get('internal_agents', 0)} internal)"
        )
        print()
        print(
            f"  {'#':<4} {'Agent':<24} {'Balance':>10} {'P/L':>10} {'Bets':>6} {'Win%':>6} {'ROI':>7}"
        )
        print(
            f"  {'─' * 4} {'─' * 24} {'─' * 10} {'─' * 10} {'─' * 6} {'─' * 6} {'─' * 7}"
        )

        for i, entry in enumerate(board, 1):
            agent = entry["agent_id"]
            if len(agent) > 23:
                agent = agent[:20] + "..."
            tag = " *" if entry.get("is_external") else ""
            balance = float(entry.get("balance", 0))
            pnl = float(entry.get("net_profit", 0))
            bets = entry.get("total_bets", 0)
            wr = entry.get("win_rate", 0)
            roi = entry.get("roi", 0)
            print(
                f"  {i:<4} {agent + tag:<24} ${balance:>9.2f} "
                f"{'+' if pnl >= 0 else ''}{pnl:>9.2f} {bets:>6} {wr:>5.0%} {roi:>6.0%}"
            )

        print()
        print("  * = external agent")
        return 0

    except httpx.ConnectError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return 1


def cmd_results(args: argparse.Namespace) -> int:
    """Show trial results."""
    trial_id = getattr(args, "trial_id", None)
    output_format = getattr(args, "format", "table")

    # Try daemon RPC first (works for connected or ended trials)
    if is_daemon_running():
        client = RPCClient(SOCKET_PATH)
        try:
            data = client.call_sync("results", trial_id=trial_id)
        except RPCError as e:
            if e.code != "NO_RESULTS":
                print(f"Error: {e.message}", file=sys.stderr)
                return 1
            data = None
        except ConnectionError:
            data = None

        if data:
            if output_format == "json":
                print(json.dumps(data, indent=2))
                return 0

            status = data.get("status", "unknown")
            ended_at = data.get("ended_at", "")
            print(f"Trial: {data.get('trial_id', trial_id or 'unknown')}")
            print(f"Status: {status}")
            if ended_at:
                print(f"Ended: {ended_at}")
            print()

            results = data.get("results", [])
            if not results:
                print("No agent results available")
                return 0

            print(
                f"  {'#':<4} {'Agent':<24} {'Balance':>10} {'P/L':>10} "
                f"{'Bets':>6} {'Win%':>6} {'ROI':>7}"
            )
            print(
                f"  {'─' * 4} {'─' * 24} {'─' * 10} {'─' * 10} "
                f"{'─' * 6} {'─' * 6} {'─' * 7}"
            )

            for i, r in enumerate(results, 1):
                agent = r.get("agent_id", "?")
                if len(agent) > 23:
                    agent = agent[:20] + "..."
                balance = float(r.get("final_balance", 0))
                pnl = float(r.get("net_profit", 0))
                bets = r.get("total_bets", 0)
                wr = r.get("win_rate", 0)
                roi = r.get("roi", 0)
                print(
                    f"  {i:<4} {agent:<24} ${balance:>9.2f} "
                    f"{'+' if pnl >= 0 else ''}{pnl:>9.2f} "
                    f"{bets:>6} {wr:>5.0%} {roi:>6.0%}"
                )
            return 0

    # Fall back to results.json on disk
    if not trial_id:
        print(
            "Trial ID required. Use 'results <trial-id>' or join a trial first.",
            file=sys.stderr,
        )
        return 1

    results_file = _trial_state_dir(trial_id) / "results.json"
    if not results_file.exists():
        print(f"No results found for trial {trial_id}", file=sys.stderr)
        return 1

    data = json.loads(results_file.read_text())
    if output_format == "json":
        print(json.dumps(data, indent=2))
    else:
        print(f"Trial: {data.get('trial_id', trial_id)}")
        print(f"Status: {data.get('status', 'unknown')}")
        ended_at = data.get("ended_at", "")
        if ended_at:
            print(f"Ended: {ended_at}")
        print()
        for i, r in enumerate(data.get("results", []), 1):
            agent = r.get("agent_id", "?")
            if len(agent) > 23:
                agent = agent[:20] + "..."
            balance = float(r.get("final_balance", 0))
            pnl = float(r.get("net_profit", 0))
            bets = r.get("total_bets", 0)
            wr = r.get("win_rate", 0)
            roi = r.get("roi", 0)
            print(
                f"  {i:<4} {agent:<24} ${balance:>9.2f} "
                f"{'+' if pnl >= 0 else ''}{pnl:>9.2f} "
                f"{bets:>6} {wr:>5.0%} {roi:>6.0%}"
            )
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover available trials from dashboard."""
    from dojozero_client._client import DojoClient

    # Use explicit --dashboard arg, otherwise DojoClient uses config
    client = DojoClient(dashboard_url=args.dashboard if args.dashboard else None)

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


def cmd_daemon_start(args: argparse.Namespace) -> int:
    """Start the daemon process (internal entry point)."""
    profile = _get_profile(args)
    profile_dir = get_profile_dir(profile)

    if is_daemon_running():
        print("Unified daemon already running")
        return 1

    if not has_api_key(profile):
        profile_hint = f" --profile {profile}" if profile else ""
        print(
            f"Error: No API key configured. Run 'dojozero-agent config{profile_hint} --api-key <key>' first.",
            file=sys.stderr,
        )
        return 1

    if args.background:
        # Start as background process
        profile_dir.mkdir(parents=True, exist_ok=True)
        log_file = profile_dir / "daemon.log"

        env = os.environ.copy()
        env.setdefault("NO_PROXY", "localhost,127.0.0.1")

        cmd = [sys.executable, "-m", "dojozero_client._cli"]
        if profile:
            cmd.extend(["--profile", profile])
        cmd.append("daemon")

        with open(log_file, "a") as f:
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        profile_msg = f" (profile: {profile})" if profile else ""
        print(f"Started unified daemon{profile_msg} (background)")
        print(f"Logs: {log_file}")
        return 0

    # Run in foreground
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    daemon = UnifiedDaemon()
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        print("\nStopping daemon...")
    return 0


def _detect_token_type(key: str) -> str:
    """Detect the type of API key/token."""
    if key.startswith("ghp_") or key.startswith("github_pat_"):
        return "GitHub PAT"
    if key.startswith("sk-agent-"):
        return "DojoZero key"
    return "unknown"


def cmd_config(args: argparse.Namespace) -> int:
    """Configure credentials and settings."""
    profile = _get_profile(args)

    # List all profiles
    if getattr(args, "list_profiles", False):
        profiles = list_profiles()
        if not profiles:
            print("No profiles configured")
            return 0
        default = get_default_profile()
        print("Configured profiles:")
        for p in profiles:
            marker = " (default)" if p == default else ""
            print(f"  {p}{marker}")
        return 0

    # Set default profile
    if getattr(args, "set_default", None):
        if set_default_profile(args.set_default):
            print(f"Default profile set to: {args.set_default}")
            return 0
        else:
            print(f"Profile '{args.set_default}' not found", file=sys.stderr)
            return 1

    # Save dashboard URL
    if getattr(args, "dashboard_url", None):
        save_config(dashboard_url=args.dashboard_url)
        print("Dashboard URL saved to ~/.dojozero/config.yaml")
        print(f"  dashboard_url: {args.dashboard_url}")
        return 0

    # Save GitHub token as API key
    if getattr(args, "github_token", None):
        save_api_key(args.github_token, profile=profile)
        profile_msg = f" (profile: {profile})" if profile else ""
        print(f"GitHub token saved to ~/.dojozero/credentials.json{profile_msg}")
        return 0

    # Save API key
    if args.api_key:
        save_api_key(args.api_key, profile=profile)
        profile_msg = f" (profile: {profile})" if profile else ""
        print(f"API key saved to ~/.dojozero/credentials.json{profile_msg}")
        return 0

    # Show current config
    if args.show:
        # Show config (dashboard URL)
        print("Configuration (~/.dojozero/config.yaml):")
        if has_config():
            client_config = load_config()
            print(f"  dashboard_url: {client_config.dashboard_url}")
        else:
            print("  (not configured - using default: http://localhost:8000)")
            print("")
            print("  To configure for remote server:")
            print("    dojozero-agent config --dashboard-url http://your-server:8000")

        # Show credentials
        print("")
        print("Credentials (~/.dojozero/credentials.json):")
        profiles = list_profiles()
        if not profiles:
            print("  (no API key configured)")
            print("")
            print("  To configure:")
            print("    dojozero-agent config --api-key <your-api-key>")
        else:
            default = get_default_profile()
            if profile:
                # Show specific profile
                key = load_api_key(profile=profile)
                if key:
                    masked = key[:10] + "..." + key[-4:] if len(key) > 14 else "****"
                    token_type = _detect_token_type(key)
                    print(f"  Profile: {profile}")
                    print(f"  API key: {masked} ({token_type})")
                else:
                    print(f"  Profile '{profile}' not found")
                    return 1
            else:
                # Show all profiles
                print(f"  Default profile: {default}")
                print(f"  Profiles: {', '.join(profiles)}")
                key = load_api_key()
                if key:
                    masked = key[:10] + "..." + key[-4:] if len(key) > 14 else "****"
                    token_type = _detect_token_type(key)
                    print(f"  API key ({default}): {masked} ({token_type})")
        return 0

    print("Usage:")
    print("  dojozero-agent config --dashboard-url <url>     # Set dashboard URL")
    print("  dojozero-agent config --api-key <key>           # Set API key")
    print("  dojozero-agent config --profile bob --api-key <key>  # Set for profile")
    print("  dojozero-agent config --show                    # Show current config")
    print("  dojozero-agent config --list-profiles           # List all profiles")
    print("  dojozero-agent config --set-default <profile>   # Set default profile")
    return 1


def cmd_leave(args: argparse.Namespace) -> int:
    """Leave a trial — unregister from server.

    Works with or without the daemon running.
    WARNING: This deletes the broker account (balance and bets are lost).
    """
    trial_id = args.trial_id

    # Try via daemon first
    if is_daemon_running():
        client = RPCClient(SOCKET_PATH)
        try:
            client.call_sync("leave", trial_id=trial_id, unregister=True)
            print(f"Left trial {trial_id} (unregistered from server)")
            return 0
        except RPCError as e:
            if e.code != "NOT_FOUND":
                print(f"Error: {e.message}", file=sys.stderr)
                return 1
            # Fall through to direct call
        except ConnectionError:
            pass  # Fall through to direct call

    # Direct call using stored state
    state_dir = _trial_state_dir(trial_id)
    state_file = state_dir / "state.json"
    if not state_file.exists():
        print(f"No state found for trial {trial_id}", file=sys.stderr)
        return 1

    state = json.loads(state_file.read_text())
    agent_id = state.get("agent_id", "")
    session_key = state.get("session_key", "")
    if not agent_id or not session_key:
        print(
            "Missing agent_id or session_key in state file. "
            "Cannot unregister without session key.",
            file=sys.stderr,
        )
        return 1

    from dojozero_client._client import DojoClient

    gateway_url = load_config().get_gateway_url(trial_id)

    try:
        result = asyncio.run(
            DojoClient.unregister_agent(gateway_url, agent_id, session_key)
        )
        print(f"Left trial {trial_id}: {result.get('message', 'OK')}")
        state["status"] = "unregistered"
        state_file.write_text(json.dumps(state, indent=2))
        return 0
    except Exception as e:
        print(f"Failed to unregister: {e}", file=sys.stderr)
        return 1


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
    parser.add_argument(
        "--profile",
        "-p",
        help="Profile name for credentials and state (default: uses default profile)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = subparsers.add_parser("start", help="Start daemon for a trial")
    p_start.add_argument("trial_id", help="Trial ID to connect to")
    p_start.add_argument(
        "--api-key",
        help="API key for authentication (required, or set $DOJOZERO_AGENT_API_KEY)",
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
    p_stop.add_argument(
        "trial_id", nargs="?", help="Trial ID (optional if only one running)"
    )
    p_stop.set_defaults(func=cmd_stop)

    # status
    p_status = subparsers.add_parser("status", help="Show daemon status")
    p_status.add_argument(
        "trial_id", nargs="?", help="Trial ID (optional if only one running)"
    )
    p_status.set_defaults(func=cmd_status)

    # logs
    p_logs = subparsers.add_parser("logs", help="Show daemon logs")
    p_logs.add_argument(
        "trial_id", nargs="?", help="Trial ID (optional if only one running)"
    )
    p_logs.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log output",
    )
    p_logs.set_defaults(func=cmd_logs)

    # bet
    p_bet = subparsers.add_parser("bet", help="Place a bet")
    p_bet.add_argument(
        "trial_id", nargs="?", help="Trial ID (optional if only one running)"
    )
    p_bet.add_argument("amount", type=float, help="Bet amount")
    p_bet.add_argument(
        "market",
        choices=["moneyline", "spread", "total"],
        help="Market type",
    )
    p_bet.add_argument("selection", help="Selection (e.g., home, away, over, under)")
    p_bet.add_argument(
        "--spread-value",
        type=float,
        default=None,
        help="Spread value for spread bets (e.g., -3.5)",
    )
    p_bet.add_argument(
        "--total-value",
        type=float,
        default=None,
        help="Total value for total bets (e.g., 215.5)",
    )
    p_bet.set_defaults(func=cmd_bet)

    # events
    p_events = subparsers.add_parser("events", help="Show event log")
    p_events.add_argument(
        "trial_id", nargs="?", help="Trial ID (optional if only one running)"
    )
    p_events.add_argument("-n", "--count", type=int, default=20, help="Number to show")
    p_events.add_argument(
        "--format",
        choices=["summary", "json"],
        default="summary",
        help="Output format (default: summary)",
    )
    p_events.add_argument(
        "--type",
        default=None,
        help="Filter by event type, comma-separated (e.g., odds_update,nba_play,nba_game_update)",
    )
    p_events.set_defaults(func=cmd_events)

    # bets
    p_bets = subparsers.add_parser("bets", help="Show bet history")
    p_bets.add_argument(
        "trial_id", nargs="?", help="Trial ID (optional if only one running)"
    )
    p_bets.add_argument("-n", "--count", type=int, default=20, help="Number to show")
    p_bets.set_defaults(func=cmd_bets)

    # list - show all running trials
    p_list = subparsers.add_parser("list", help="List running trials")
    p_list.set_defaults(func=cmd_list)

    # leaderboard
    p_lb = subparsers.add_parser("leaderboard", help="Show trial leaderboard")
    p_lb.add_argument(
        "trial_id", nargs="?", help="Trial ID (optional if connected to a trial)"
    )
    p_lb.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    p_lb.set_defaults(func=cmd_leaderboard)

    # results
    p_results = subparsers.add_parser("results", help="Show trial results")
    p_results.add_argument(
        "trial_id", nargs="?", help="Trial ID (optional if only one running)"
    )
    p_results.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    p_results.set_defaults(func=cmd_results)

    # discover
    p_discover = subparsers.add_parser("discover", help="Discover available trials")
    p_discover.add_argument(
        "--dashboard",
        "-d",
        help="Dashboard URL (default: $DOJOZERO_DASHBOARD_URL)",
    )
    p_discover.set_defaults(func=cmd_discover)

    # daemon - start daemon process (internal, used by auto-start)
    p_daemon = subparsers.add_parser(
        "daemon", help="Start daemon process (usually auto-started by 'start')"
    )
    p_daemon.add_argument(
        "--background",
        "-b",
        action="store_true",
        help="Run in background",
    )
    p_daemon.set_defaults(func=cmd_daemon_start)

    # config - configure credentials and settings
    p_config = subparsers.add_parser(
        "config", help="Configure credentials and settings"
    )
    p_config.add_argument(
        "--dashboard-url",
        help="Set dashboard server URL (stored in ~/.dojozero/config.yaml)",
    )
    p_config.add_argument(
        "--api-key",
        help="Set API key (stored securely in ~/.dojozero/credentials.json)",
    )
    p_config.add_argument(
        "--github-token",
        help="Set GitHub Personal Access Token as API key",
    )
    p_config.add_argument(
        "--show",
        action="store_true",
        help="Show current configuration",
    )
    p_config.add_argument(
        "--list-profiles",
        action="store_true",
        help="List all configured profiles",
    )
    p_config.add_argument(
        "--set-default",
        metavar="PROFILE",
        help="Set the default profile",
    )
    p_config.set_defaults(func=cmd_config)

    # leave - leave a trial
    p_leave = subparsers.add_parser(
        "leave",
        help="Leave a trial and unregister from server (balance/bets lost)",
    )
    p_leave.add_argument("trial_id", help="Trial ID to leave")
    p_leave.set_defaults(func=cmd_leave)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
