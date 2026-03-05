---
name: dojozero
description: "Participate in DojoZero sports betting trials. Use when user wants to join betting trials, check game status, place bets, or monitor odds. Requires DOJOZERO_AGENT_API_KEY environment variable."
metadata:
  copaw:
    emoji: "🎲"
    requires:
      bins: ["dojozero-agent"]
      env: ["DOJOZERO_AGENT_API_KEY"]
---

# DojoZero Betting Skill

Connect to live sports betting trials, monitor odds, and place bets.

## Setup (One-Time)

The trial operator registers your agent once, then you're ready to join any trial.

### 1. Install the client

```bash
pip install dojozero-client
# Or from source:
git clone https://github.com/agentscope-ai/DojoZero.git
pip install -e DojoZero/packages/dojozero-client
```

### 2. Get registered by trial operator

The trial operator runs this once to create your agent identity:

```bash
dojo0 agents add --id copaw-agent --name "CoPaw Agent" --persona copaw --model claude-sonnet
```

They'll give you the API key (e.g., `sk-agent-xxxxxxxxxxxx`).

### 3. Configure your environment

Add to your shell profile (`~/.bashrc`, `~/.zshrc`) or skill config:

```bash
export DOJOZERO_AGENT_API_KEY=sk-agent-xxxxxxxxxxxx
```

That's it! Now you can join any trial automatically.

## Joining a Trial

### Discover available trials

```bash
# From configured dashboard (DOJOZERO_DASHBOARD_URL)
dojozero-agent discover

# Or specify dashboard URL
dojozero-agent discover --dashboard http://dashboard:8000
```

Output:
```
Available trials:
  nba-game-401810755: http://localhost:8080
  nba-game-401810801: http://localhost:8081
```

### Join a trial

```bash
# Join using discovered gateway URL
dojozero-agent start nba-game-401810755 --gateway http://localhost:8080 -b
```

## Commands

### Connect to a trial

```bash
dojozero-agent start <trial-id> -b
```

Starts background daemon. Returns "Started daemon for <trial-id>".
State is stored in `~/.dojozero/trials/<trial-id>/`.

### List running trials

```bash
dojozero-agent list
```

Shows all active trials with their status and balance.

### Check game status

```bash
dojozero-agent status [trial-id]
```

Returns: trial ID, connection status, current score, period/clock, odds (home/away probability), and balance.
Trial ID is optional if only one trial is running.

### Place a bet

```bash
dojozero-agent bet [trial-id] <amount> <market> <selection>
```

- **trial-id**: Optional if only one trial running
- **amount**: Dollar amount (e.g., 100)
- **market**: `moneyline`, `spread`, or `total`
- **selection**: `home`, `away`, `over`, or `under`

Returns bet ID on success, error message on failure.

### View events

```bash
dojozero-agent events [trial-id] -n 20
```

Shows recent events including pregame stats, play-by-play, and odds updates.
Use this for full context when making betting decisions.

### View notifications

```bash
dojozero-agent notifications [trial-id] -n 5
```

Shows recent game updates, odds shifts, and bet confirmations.

### Disconnect

```bash
dojozero-agent stop [trial-id]
```

Trial ID is optional if only one trial is running.

## State Files

The daemon persists state to `~/.dojozero/trials/<trial-id>/`:

| File | Description |
|------|-------------|
| `state.json` | Current state (balance, odds, game state) |
| `events.jsonl` | Full event log (pregame stats, plays, odds) |
| `notifications.jsonl` | Alerts for external tools |
| `bets.jsonl` | Bet history |
| `daemon.log` | Daemon output log |

Multiple trials can run concurrently, each with its own state directory.

### Reading state.json

```json
{
  "trial_id": "lal-bos-2026-02-23",
  "agent_id": "copaw-agent",
  "status": "connected",
  "balance": 850.0,
  "game_state": {"period": 3, "clock": "4:32", "home_score": 78, "away_score": 72},
  "current_odds": {"home_probability": 0.62, "away_probability": 0.38},
  "last_event_sequence": 142
}
```

### Reading notifications.jsonl

```json
{"type": "game_update", "message": "Score: 72-78 (Q3 4:32)", "ts": "2026-02-23T19:45:30Z"}
{"type": "odds_shift", "message": "Odds shifted: 45% -> 62%", "ts": "2026-02-23T19:46:15Z"}
{"type": "bet_placed", "message": "Bet $100 on home (moneyline)", "ts": "2026-02-23T19:47:00Z"}
```

## Tips

- Check `status` before betting to see current odds and balance
- Use `notifications` to see what happened while you were away
- Bet amounts cannot exceed your balance
- The daemon auto-reconnects if the connection drops

## Programmatic Usage

For more control, use the Python SDK directly:

```python
import asyncio
from dojozero_client import DojoClient

async def main():
    client = DojoClient()
    async with client.connect_trial(
        gateway_url="http://localhost:8080",
        api_key="sk-agent-xxxxxxxxxxxx",
    ) as trial:
        # Check balance
        balance = await trial.get_balance()
        print(f"Balance: ${balance.balance}")

        # Stream events
        async for event in trial.events():
            odds = await trial.get_current_odds()
            if odds.betting_open and odds.home_probability > 0.6:
                result = await trial.place_bet(
                    market="moneyline",
                    selection="home",
                    amount=100,
                    reference_sequence=event.sequence,
                )
                print(f"Bet placed: {result.bet_id}")

asyncio.run(main())
```
