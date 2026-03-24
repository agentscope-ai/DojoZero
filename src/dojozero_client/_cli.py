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
    Daemon,
    DaemonConfig,
    UnifiedDaemon,
    get_daemon_status,
    is_daemon_running,
    is_unified_daemon_running,
    list_running_trials,
    stop_daemon,
    stop_unified_daemon,
    _trial_state_dir,
)
from dojozero_client._rpc import RPCClient, RPCError

logger = logging.getLogger(__name__)


def _get_state_dir(args: argparse.Namespace) -> Path:
    """Get state directory from args.

    Priority:
    1. Explicit --state-dir override
    2. Computed from trial_id: ~/.dojozero/trials/{trial_id}/
    3. Auto-detect if only one trial is running
    4. Legacy fallback: ~/.dojozero/
    """
    if args.state_dir:
        return Path(args.state_dir)
    if hasattr(args, "trial_id") and args.trial_id:
        return _trial_state_dir(args.trial_id)
    # Auto-detect if only one trial running
    running = list_running_trials()
    if len(running) == 1:
        return _trial_state_dir(running[0])
    return CONFIG_DIR


def _get_profile(args: argparse.Namespace) -> str | None:
    """Get profile from args or environment."""
    return getattr(args, "profile", None) or os.environ.get("DOJOZERO_PROFILE")


def cmd_start(args: argparse.Namespace) -> int:
    """Start the daemon for a trial."""
    profile = _get_profile(args)
    state_dir = _get_state_dir(args)

    if is_daemon_running(trial_id=args.trial_id, state_dir=state_dir):
        print(
            f"Daemon already running for {args.trial_id}. Use 'stop' first.",
            file=sys.stderr,
        )
        return 1

    api_key = (
        args.api_key
        or os.environ.get("DOJOZERO_AGENT_API_KEY", "")
        or load_api_key(profile=profile)
        or ""
    )
    if not api_key:
        profile_hint = f" --profile {profile}" if profile else ""
        print(
            f"Error: API key required. Use 'dojozero-agent config{profile_hint} --api-key <key>', "
            "set DOJOZERO_AGENT_API_KEY, or pass --api-key.",
            file=sys.stderr,
        )
        return 1

    daemon_config = DaemonConfig(
        trial_id=args.trial_id,
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
        ]
        # Note: API key is NOT passed via CLI for security (visible in ps).
        # Child process reads from credentials file or DOJOZERO_AGENT_API_KEY env var.
        if daemon_config.strategy:
            cmd.extend(["--strategy", daemon_config.strategy])
        if daemon_config.auto_bet:
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

    daemon = Daemon(daemon_config)
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        print("\nStopping daemon...")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Stop the running daemon."""
    trial_id = getattr(args, "trial_id", None)
    state_dir = _get_state_dir(args)

    if not is_daemon_running(trial_id=trial_id, state_dir=state_dir):
        print(f"No daemon running{f' for {trial_id}' if trial_id else ''}")
        return 1

    if stop_daemon(trial_id=trial_id, state_dir=state_dir):
        print(f"Daemon stopped{f' for {trial_id}' if trial_id else ''}")
        return 0
    else:
        print("Failed to stop daemon", file=sys.stderr)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show current daemon status."""
    trial_id = getattr(args, "trial_id", None)
    state_dir = _get_state_dir(args)
    state = get_daemon_status(trial_id=trial_id, state_dir=state_dir)

    if not state:
        print(
            f"No active trial{f' for {trial_id}' if trial_id else ''}. Use 'start <trial-id>' first."
        )
        return 1

    running = is_daemon_running(trial_id=trial_id, state_dir=state_dir)
    game_state = state.get("game_state", {})
    odds = state.get("current_odds", {})

    print(f"Trial: {state.get('trial_id', 'unknown')}")
    print(f"Agent: {state.get('agent_id', 'unknown')}")
    status_label = state.get("status", "unknown")
    if running:
        print(f"Status: {status_label} (daemon running)")
    else:
        print(f"Status: {status_label} (daemon not running)")

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

    # Fetch fresh balance from server if possible
    balance = state.get("balance", 0)
    trial_id_val = state.get("trial_id")
    agent_id = state.get("agent_id")
    if trial_id_val and agent_id:
        gateway_url = load_config().get_gateway_url(trial_id_val)
        fresh_balance = _fetch_server_balance(gateway_url, agent_id)
        if fresh_balance is not None:
            balance = fresh_balance
            # Update local state if different
            if balance != state.get("balance", 0):
                _update_local_balance(state_dir, balance)

    print(f"Balance: ${balance:.2f}")
    print(f"Last Update: {state.get('last_updated', 'never')}")

    # Show bet history
    bets_file = state_dir / "bets.jsonl"
    if bets_file.exists():
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
                ts = b.get("placed_at", "")[:16]  # Trim to minute
                prob_str = f" @ {prob:.0%}" if prob else ""
                print(f"  ${amt:.0f} on {selection} ({market}){prob_str} - {ts}")

    return 0


def _fetch_server_balance(gateway_url: str, agent_id: str) -> float | None:
    """Fetch fresh balance from server."""
    import httpx

    try:
        resp = httpx.get(
            f"{gateway_url}/balance",
            headers={"X-Agent-ID": agent_id},
            timeout=5.0,
        )
        if resp.status_code == 200:
            return float(resp.json().get("balance", 0))
    except Exception:
        pass
    return None


def _update_local_balance(state_dir: Path, balance: float) -> None:
    """Update balance in local state.json."""
    from datetime import datetime

    state_file = state_dir / "state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            state["balance"] = balance
            state["last_updated"] = datetime.now().isoformat()
            state_file.write_text(json.dumps(state, indent=2))
        except Exception:
            pass


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
    """Place a bet via daemon RPC or REST API."""
    trial_id = getattr(args, "trial_id", None)
    state_dir = _get_state_dir(args)
    state = get_daemon_status(trial_id=trial_id, state_dir=state_dir)

    if not state:
        print("No active trial. Use 'start <trial-id>' first.", file=sys.stderr)
        return 1

    actual_trial_id: str = state.get("trial_id", trial_id or "")

    # Try unified daemon RPC first (keeps local state in sync)
    if is_unified_daemon_running():
        client = RPCClient(SOCKET_PATH)
        try:
            result = client.call_sync(
                "bet",
                trial_id=actual_trial_id,
                amount=args.amount,
                market=args.market,
                selection=args.selection,
            )
            print(f"Bet placed: ${args.amount} on {args.selection} ({args.market})")
            print(f"Bet ID: {result.get('bet_id')}")
            return 0
        except RPCError as e:
            print(f"Error: {e.message}", file=sys.stderr)
            return 1

    # Fallback to direct REST API (legacy per-trial daemon mode)
    import httpx

    gateway_url = load_config().get_gateway_url(actual_trial_id)
    agent_id = state.get("agent_id", "")

    try:
        resp = httpx.post(
            f"{gateway_url}/bets",
            headers={"X-Agent-ID": agent_id},
            json={
                "market": args.market,
                "selection": args.selection,
                "amount": str(args.amount),
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
        bet_id = data.get("betId")
        print(f"Bet placed: ${args.amount} on {args.selection} ({args.market})")
        print(f"Bet ID: {bet_id}")

        # Update local state for legacy mode
        _update_local_state_after_bet(state_dir, gateway_url, agent_id, data)

        return 0

    except httpx.ConnectError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return 1


def _update_local_state_after_bet(
    state_dir: Path, gateway_url: str, agent_id: str, bet_data: dict[str, Any]
) -> None:
    """Update local state after placing a bet (legacy mode).

    Fetches fresh balance from server and updates state.json and bets.jsonl.
    """
    import httpx
    from datetime import datetime

    # Fetch fresh balance
    try:
        resp = httpx.get(
            f"{gateway_url}/agents/balance",
            headers={"X-Agent-ID": agent_id},
            timeout=5.0,
        )
        if resp.status_code == 200:
            balance_data = resp.json()
            new_balance = float(balance_data.get("balance", 0))

            # Update state.json
            state_file = state_dir / "state.json"
            if state_file.exists():
                state = json.loads(state_file.read_text())
                state["balance"] = new_balance
                state["last_updated"] = datetime.now().isoformat()
                state_file.write_text(json.dumps(state, indent=2))
    except Exception:
        pass  # Best effort - don't fail the bet command

    # Append to bets.jsonl
    try:
        bets_file = state_dir / "bets.jsonl"
        bet_record = {
            "bet_id": bet_data.get("betId"),
            "market": bet_data.get("market"),
            "selection": bet_data.get("selection"),
            "amount": float(bet_data.get("amount", 0)),
            "probability": float(bet_data.get("probability", 0)),
            "status": bet_data.get("status", "placed"),
            "placed_at": datetime.now().isoformat(),
        }
        with open(bets_file, "a") as f:
            f.write(json.dumps(bet_record) + "\n")
    except Exception:
        pass  # Best effort


def cmd_notifications(args: argparse.Namespace) -> int:
    """Show recent notifications."""
    state_dir = _get_state_dir(args)
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
    state_dir = _get_state_dir(args)
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
    """List all running trials with fresh balances."""
    # Try RPC first for live data with refreshed balances
    if is_unified_daemon_running():
        client = RPCClient(SOCKET_PATH)
        try:
            result = client.call_sync("list")
            trials = result.get("trials", {})

            if not trials:
                print("No trials connected")
                return 0

            print(f"Running trials ({len(trials)}):")
            for trial_id, info in trials.items():
                status = "connected" if info.get("connected") else "disconnected"
                balance = info.get("balance", 0)
                print(f"  {trial_id}: {status}, balance=${balance:.2f}")
            return 0
        except (RPCError, ConnectionError):
            pass  # Fall back to disk-based status

    # Fall back to reading from disk (may be stale)
    running = list_running_trials()

    if not running:
        print("No daemons running")
        return 0

    print(f"Running trials ({len(running)}):")
    for trial_id in running:
        state = get_daemon_status(trial_id=trial_id)
        if state:
            status = state.get("status", "unknown")
            balance = state.get("balance", 0)
            agent_id = state.get("agent_id")
            if agent_id:
                gateway_url = load_config().get_gateway_url(trial_id)
                fresh = _fetch_server_balance(gateway_url, agent_id)
                if fresh is not None:
                    balance = fresh
            print(f"  {trial_id}: {status}, balance=${balance:.2f}")
        else:
            print(f"  {trial_id}: (status unknown)")

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


# =============================================================================
# Unified Daemon Commands (New Architecture)
# =============================================================================


def cmd_daemon_start(args: argparse.Namespace) -> int:
    """Start the unified daemon."""
    profile = _get_profile(args)
    profile_dir = get_profile_dir(profile)

    if is_unified_daemon_running():
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


def cmd_daemon_stop(_: argparse.Namespace) -> int:
    """Stop the unified daemon."""
    if not is_unified_daemon_running():
        print("Unified daemon not running")
        return 1

    if stop_unified_daemon():
        print("Unified daemon stopped")
        return 0
    else:
        print("Failed to stop daemon", file=sys.stderr)
        return 1


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


def cmd_join(args: argparse.Namespace) -> int:
    """Join a trial via unified daemon."""
    if not is_unified_daemon_running():
        print(
            "Unified daemon not running. Start with 'dojozero-agent daemon -b'",
            file=sys.stderr,
        )
        return 1

    client = RPCClient(SOCKET_PATH)
    try:
        result = client.call_sync(
            "join",
            trial_id=args.trial_id,
        )
        print(f"Joined trial {args.trial_id} as {result.get('agent_id')}")
        return 0
    except RPCError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        return 1
    except ConnectionError as e:
        print(f"Cannot connect to daemon: {e}", file=sys.stderr)
        return 1


def cmd_leave(args: argparse.Namespace) -> int:
    """Leave a trial via unified daemon."""
    if not is_unified_daemon_running():
        print("Unified daemon not running", file=sys.stderr)
        return 1

    client = RPCClient(SOCKET_PATH)
    try:
        client.call_sync("leave", trial_id=args.trial_id)
        print(f"Left trial {args.trial_id}")
        return 0
    except RPCError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        return 1
    except ConnectionError as e:
        print(f"Cannot connect to daemon: {e}", file=sys.stderr)
        return 1


def cmd_rpc_bet(args: argparse.Namespace) -> int:
    """Place a bet via unified daemon RPC."""
    if not is_unified_daemon_running():
        print("Unified daemon not running", file=sys.stderr)
        return 1

    client = RPCClient(SOCKET_PATH)
    try:
        result = client.call_sync(
            "bet",
            trial_id=args.trial_id,
            amount=args.amount,
            market=args.market,
            selection=args.selection,
        )
        print(f"Bet placed: ${args.amount} on {args.selection} ({args.market})")
        print(f"Bet ID: {result.get('bet_id')}")
        return 0
    except RPCError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        return 1
    except ConnectionError as e:
        print(f"Cannot connect to daemon: {e}", file=sys.stderr)
        return 1


def cmd_rpc_status(args: argparse.Namespace) -> int:
    """Get status via unified daemon RPC."""
    if not is_unified_daemon_running():
        print("Unified daemon not running", file=sys.stderr)
        return 1

    client = RPCClient(SOCKET_PATH)
    try:
        trial_id = getattr(args, "trial_id", None)
        result = client.call_sync("status", trial_id=trial_id)

        print(f"Trial: {result.get('trial_id', 'unknown')}")
        print(f"Agent: {result.get('agent_id', 'unknown')}")
        print(f"Status: {result.get('status', 'unknown')}")
        print(f"Balance: ${result.get('balance', 0):.2f}")

        odds = result.get("current_odds", {})
        if odds:
            print(
                f"Odds: Home {odds.get('home_probability', 0):.0%}, Away {odds.get('away_probability', 0):.0%}"
            )

        print(f"Last Update: {result.get('last_updated', 'never')}")
        return 0
    except RPCError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        return 1
    except ConnectionError as e:
        print(f"Cannot connect to daemon: {e}", file=sys.stderr)
        return 1


def cmd_rpc_list(_: argparse.Namespace) -> int:
    """List trials via unified daemon RPC."""
    if not is_unified_daemon_running():
        print("Unified daemon not running", file=sys.stderr)
        return 1

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
    except ConnectionError as e:
        print(f"Cannot connect to daemon: {e}", file=sys.stderr)
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
    p_bet.set_defaults(func=cmd_bet)

    # notifications
    p_notif = subparsers.add_parser("notifications", help="Show notifications")
    p_notif.add_argument(
        "trial_id", nargs="?", help="Trial ID (optional if only one running)"
    )
    p_notif.add_argument("-n", "--count", type=int, default=10, help="Number to show")
    p_notif.set_defaults(func=cmd_notifications)

    # events
    p_events = subparsers.add_parser("events", help="Show event log")
    p_events.add_argument(
        "trial_id", nargs="?", help="Trial ID (optional if only one running)"
    )
    p_events.add_argument("-n", "--count", type=int, default=20, help="Number to show")
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

    # discover
    p_discover = subparsers.add_parser("discover", help="Discover available trials")
    p_discover.add_argument(
        "--dashboard",
        "-d",
        help="Dashboard URL (default: $DOJOZERO_DASHBOARD_URL)",
    )
    p_discover.set_defaults(func=cmd_discover)

    # =========================================================================
    # Unified Daemon Commands (New Architecture)
    # =========================================================================

    # daemon - start unified daemon
    p_daemon = subparsers.add_parser(
        "daemon", help="Start unified daemon (manages multiple trials)"
    )
    p_daemon.add_argument(
        "--background",
        "-b",
        action="store_true",
        help="Run in background",
    )
    p_daemon.set_defaults(func=cmd_daemon_start)

    # daemon-stop - stop unified daemon
    p_daemon_stop = subparsers.add_parser("daemon-stop", help="Stop the unified daemon")
    p_daemon_stop.set_defaults(func=cmd_daemon_stop)

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

    # join - join a trial via daemon RPC
    p_join = subparsers.add_parser("join", help="Join a trial (via unified daemon)")
    p_join.add_argument("trial_id", help="Trial ID to join")
    p_join.set_defaults(func=cmd_join)

    # leave - leave a trial via daemon RPC
    p_leave = subparsers.add_parser("leave", help="Leave a trial (via unified daemon)")
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
