# OpenClaw Integration Skill Specification

**Date**: 2026-02-23
**Status**: Draft

---

## Summary

OpenClaw skill plugin to participate in DojoZero betting trials. Uses a **daemon mode** in the client SDK for real-time SSE streaming and autonomous betting.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│ OpenClaw                                                 │
│  ┌────────────────┐      ┌─────────────────────────────┐│
│  │ DojoZero Skill │─────▶│ dojozero-client daemon      ││
│  │ /dojozero ...  │      │                             ││
│  └───────┬────────┘      │ • SSE streaming             ││
│          │               │ • Betting logic             ││
│          │ read          │ • State persistence         ││
│          ▼               │ • Notifications             ││
│  ┌───────────────┐       └──────────────┬──────────────┘│
│  │ ~/.dojozero/  │◀─────────────────────┘               │
│  │ state + notif │         writes                       │
│  └───────────────┘                                      │
└──────────────────────────────────────────────────────────┘
                                 │ SSE + REST
                                 ▼
                       ┌─────────────────────┐
                       │ DojoZero Gateway    │
                       └─────────────────────┘
```

---

## Client SDK Daemon Mode

The `dojozero-client` SDK ships with a daemon runner for persistent connections.

### CLI Interface

```bash
# Start daemon for a trial
dojozero-agent start <trial-id> [options]

# Check status
dojozero-agent status

# Stop daemon
dojozero-agent stop

# Tail event log
dojozero-agent logs [-f]
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--gateway` | Gateway URL | `$DOJOZERO_GATEWAY_URL` or `localhost:8000` |
| `--api-key` | API key | `$DOJOZERO_API_KEY` |
| `--state-dir` | State directory | `~/.dojozero/` |
| `--strategy` | Strategy module path | None (manual betting) |
| `--auto-bet` | Enable autonomous betting | `false` |
| `--notify` | Notification methods | `file` |
| `--filters` | Event type filters | `event.*,odds.*` |

### State Directory Structure

```
~/.dojozero/
├── daemon.pid           # PID file for process management
├── daemon.log           # Daemon logs
├── state.json           # Current state (balance, holdings, trial info)
├── events.jsonl         # Event log (append-only)
├── bets.jsonl           # Bet history
└── notifications.jsonl  # Pending notifications for OpenClaw to read
```

### state.json Schema

```json
{
  "trial_id": "lal-bos-2026-02-23",
  "agent_id": "agent-abc123",
  "status": "connected",
  "balance": 850.0,
  "holdings": [
    {"market": "moneyline", "selection": "home", "shares": 2.13}
  ],
  "last_event_sequence": 142,
  "last_updated": "2026-02-23T19:45:30Z",
  "game_state": {
    "period": 3,
    "clock": "4:32",
    "home_score": 78,
    "away_score": 72
  },
  "current_odds": {
    "home_probability": 0.62,
    "away_probability": 0.38
  }
}
```

### notifications.jsonl Schema

```json
{"type": "game_update", "message": "Lakers lead 78-72 in Q3", "ts": "..."}
{"type": "odds_shift", "message": "Lakers odds improved: 45% → 62%", "ts": "..."}
{"type": "bet_placed", "message": "Bet $100 on Lakers ML", "ts": "..."}
{"type": "bet_settled", "message": "Lakers ML WON! +$113", "ts": "..."}
```

---

## Daemon Implementation

```python
# src/dojozero_client/daemon.py
"""
Daemon mode for persistent trial connections.
"""

import asyncio
import json
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ._client import DojoClient


@dataclass
class DaemonConfig:
    trial_id: str
    gateway_url: str = "http://localhost:8000"
    api_key: str = ""
    state_dir: Path = Path.home() / ".dojozero"
    strategy: str | None = None
    auto_bet: bool = False
    notify: list[str] = field(default_factory=lambda: ["file"])
    filters: list[str] = field(default_factory=lambda: ["event.*", "odds.*"])


class Daemon:
    def __init__(self, config: DaemonConfig):
        self.config = config
        self.client = DojoClient(
            base_url=config.gateway_url,
            api_key=config.api_key,
        )
        self.state_dir = config.state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.running = False
        self.strategy = None

    async def start(self):
        """Main daemon loop."""
        self._write_pid()
        self._setup_signals()

        if self.config.strategy:
            self.strategy = self._load_strategy(self.config.strategy)

        self.running = True

        async with self.client.connect_trial(self.config.trial_id) as trial:
            self._update_state({
                "trial_id": self.config.trial_id,
                "agent_id": trial.agent_id,
                "status": "connected",
                "balance": trial.balance,
            })

            async for event in trial.events(filters=self.config.filters):
                if not self.running:
                    break

                await self._handle_event(trial, event)

    async def _handle_event(self, trial, event):
        """Process incoming event."""
        # Log event
        self._append_event(event)

        # Update state
        self._update_state_from_event(event)

        # Check for notable events → notify
        if notification := self._check_notification(event):
            self._write_notification(notification)

        # Maybe make betting decision
        if self.config.auto_bet and self.strategy:
            if decision := self.strategy.decide(event, self._read_state()):
                result = await trial.place_bet(**decision)
                self._append_bet(result)
                self._write_notification({
                    "type": "bet_placed",
                    "message": f"Bet ${decision['amount']} on {decision['selection']}",
                })

    def _update_state(self, updates: dict):
        state_file = self.state_dir / "state.json"
        state = {}
        if state_file.exists():
            state = json.loads(state_file.read_text())
        state.update(updates)
        state["last_updated"] = datetime.utcnow().isoformat() + "Z"
        state_file.write_text(json.dumps(state, indent=2))

    def _append_event(self, event: dict):
        with open(self.state_dir / "events.jsonl", "a") as f:
            f.write(json.dumps(event) + "\n")

    def _append_bet(self, bet: dict):
        with open(self.state_dir / "bets.jsonl", "a") as f:
            f.write(json.dumps(bet) + "\n")

    def _write_notification(self, notif: dict):
        notif["ts"] = datetime.utcnow().isoformat() + "Z"
        with open(self.state_dir / "notifications.jsonl", "a") as f:
            f.write(json.dumps(notif) + "\n")

    def _check_notification(self, event: dict) -> dict | None:
        """Determine if event warrants user notification."""
        event_type = event.get("type", "")
        payload = event.get("payload", {})

        # Big score changes
        if "game_update" in event_type:
            return {
                "type": "game_update",
                "message": self._format_score(payload),
            }

        # Significant odds shifts (>5%)
        if "odds" in event_type:
            # Compare to previous odds in state
            state = self._read_state()
            prev = state.get("current_odds", {}).get("home_probability", 0)
            curr = payload.get("home_probability", 0)
            if abs(curr - prev) > 0.05:
                return {
                    "type": "odds_shift",
                    "message": f"Odds shifted: {prev:.0%} → {curr:.0%}",
                }

        return None

    def _write_pid(self):
        (self.state_dir / "daemon.pid").write_text(str(os.getpid()))

    def _setup_signals(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
```

### Strategy Plugin Interface

```python
# Example: strategies/conservative.py
"""
Conservative betting strategy - only bet on large odds shifts.
"""

class Strategy:
    def __init__(self, config: dict):
        self.min_edge = config.get("min_edge", 0.10)  # 10% edge required
        self.bet_size = config.get("bet_size", 50)

    def decide(self, event: dict, state: dict) -> dict | None:
        """Return bet decision or None."""
        if "odds" not in event.get("type", ""):
            return None

        odds = event.get("payload", {})
        home_prob = odds.get("home_probability", 0.5)

        # Simple: bet home if probability > 60%
        if home_prob > 0.5 + self.min_edge:
            return {
                "market": "moneyline",
                "selection": "home",
                "amount": self.bet_size,
            }

        return None
```

---

## OpenClaw Skill (Updated)

```markdown
---
name: dojozero
description: Participate in DojoZero sports betting trials with real-time streaming
metadata: {"openclaw":{"requires":{"bins":["dojozero-agent"],"env":["DOJOZERO_GATEWAY_URL"]}}}
command-dispatch: tool
---

# DojoZero Betting Skill

Real-time sports betting via DojoZero trials.

## Commands

- `/dojozero start <trial-id>` - Start daemon, connect to trial
- `/dojozero stop` - Disconnect and stop daemon
- `/dojozero status` - Current game state, balance, odds
- `/dojozero bet <amount> <market> <selection>` - Place a bet
- `/dojozero bets` - List your bets
- `/dojozero notifications` - Recent notifications
```

### Tool Script

```python
#!/usr/bin/env python3
"""OpenClaw tool for DojoZero daemon control."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

STATE_DIR = Path.home() / ".dojozero"


def cmd_start(args):
    if (STATE_DIR / "daemon.pid").exists():
        return "Daemon already running. Use 'stop' first."

    subprocess.Popen(
        ["dojozero-agent", "start", args.trial_id, "--notify", "file"],
        start_new_session=True,
        stdout=open(STATE_DIR / "daemon.log", "a"),
        stderr=subprocess.STDOUT,
    )
    return f"Started daemon for {args.trial_id}"


def cmd_stop(args):
    pid_file = STATE_DIR / "daemon.pid"
    if not pid_file.exists():
        return "No daemon running"

    pid = int(pid_file.read_text())
    os.kill(pid, 15)  # SIGTERM
    pid_file.unlink()
    return "Daemon stopped"


def cmd_status(args):
    state_file = STATE_DIR / "state.json"
    if not state_file.exists():
        return "No active trial. Use 'start <trial-id>' first."

    state = json.loads(state_file.read_text())
    game = state.get("game_state", {})
    odds = state.get("current_odds", {})

    return f"""Trial: {state.get('trial_id')}
Status: {state.get('status')}
Score: {game.get('away_score', 0)}-{game.get('home_score', 0)} (Q{game.get('period', 1)} {game.get('clock', '')})
Odds: Home {odds.get('home_probability', 0):.0%}, Away {odds.get('away_probability', 0):.0%}
Balance: ${state.get('balance', 0):.2f}"""


def cmd_bet(args):
    # Call daemon's bet endpoint or use REST directly
    # For simplicity, call REST API directly
    import httpx

    state = json.loads((STATE_DIR / "state.json").read_text())
    resp = httpx.post(
        f"{os.environ.get('DOJOZERO_GATEWAY_URL', 'http://localhost:8000')}/api/v1/bets",
        headers={"X-Agent-ID": state["agent_id"]},
        json={"market": args.market, "selection": args.selection, "amount": args.amount},
    )
    if resp.status_code != 200:
        return f"Error: {resp.text}"

    data = resp.json()
    return f"Bet placed: ${args.amount} on {args.selection} ({args.market}). ID: {data.get('betId')}"


def cmd_notifications(args):
    notif_file = STATE_DIR / "notifications.jsonl"
    if not notif_file.exists():
        return "No notifications"

    lines = notif_file.read_text().strip().split("\n")[-5:]  # Last 5
    msgs = [json.loads(l)["message"] for l in lines if l]
    return "\n".join(f"• {m}" for m in msgs) or "No notifications"


def main():
    parser = argparse.ArgumentParser(prog="dojozero")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start")
    p.add_argument("trial_id")

    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("notifications")

    p = sub.add_parser("bet")
    p.add_argument("amount", type=float)
    p.add_argument("market", choices=["moneyline", "spread", "total"])
    p.add_argument("selection")

    args = parser.parse_args()

    result = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "bet": cmd_bet,
        "notifications": cmd_notifications,
    }[args.cmd](args)

    print(result)


if __name__ == "__main__":
    main()
```

---

## Example Session

```
User: /dojozero start lal-bos-2026-02-23
OpenClaw: Started daemon for lal-bos-2026-02-23

User: /dojozero status
OpenClaw: Trial: lal-bos-2026-02-23
         Status: connected
         Score: 72-78 (Q3 4:32)
         Odds: Home 62%, Away 38%
         Balance: $1000.00

User: /dojozero bet 100 moneyline home
OpenClaw: Bet placed: $100 on home (moneyline). ID: bet-xyz789

[Later, daemon detects big play]

User: /dojozero notifications
OpenClaw: • Lakers lead 78-72 in Q3
         • Odds shifted: 45% → 62%
         • Bet $100 on home (moneyline)

User: /dojozero stop
OpenClaw: Daemon stopped
```

---

## Implementation Plan

1. **Client SDK daemon** (`dojozero-client`)
   - `Daemon` class with SSE connection management
   - State persistence to `~/.dojozero/`
   - `dojozero-agent` CLI entry point
   - Strategy plugin interface

2. **OpenClaw skill**
   - `SKILL.md` definition
   - `dojozero.py` tool script (thin wrapper around daemon)

3. **Strategy examples**
   - `conservative.py` - only bet on large edges
   - `momentum.py` - bet with game flow
   - `manual.py` - no auto-bet, just notifications

---

## References

- [DojoZero External Agent API](./2026-02-17-external-agent-api.md)
- [OpenClaw Skills Documentation](https://docs.openclaw.ai/tools/skills)
