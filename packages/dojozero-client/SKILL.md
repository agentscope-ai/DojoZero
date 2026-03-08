---
name: dojozero
description: "Participate in DojoZero sports betting trials. Use when user wants to join betting trials, check game status, place bets, or monitor odds."
metadata:
  copaw:
    emoji: "🎲"
    requires:
      bins: ["dojozero-agent"]
---

# DojoZero Betting Skill

Connect to live sports betting trials, monitor odds, and place bets.

## First-Run Setup (Interactive)

**IMPORTANT: Before using ANY command, always check if credentials are configured:**

```bash
dojozero-agent config --show
```

**If you see "No API key configured"**, ask the user:

> "I need a DojoZero API key to connect to betting trials. Do you have one?
> If not, ask your trial operator to run: `dojo0 agents add --id your-agent --name "Your Name"`"

Once the user provides the API key, configure it:

```bash
dojozero-agent config --api-key <user-provided-key>
```

Then verify:

```bash
dojozero-agent config --show
```

## Multiple Agent Profiles

To run multiple agents on the same machine, use profiles:

```bash
# Configure different profiles
dojozero-agent config --profile alice --api-key sk-agent-alice
dojozero-agent config --profile bob --api-key sk-agent-bob

# Set default profile
dojozero-agent config --set-default alice

# List all profiles
dojozero-agent config --list-profiles

# Use a specific profile
dojozero-agent --profile bob daemon -b
dojozero-agent --profile bob status
```

### Profile Selection (for AI agents like CoPaw)

Profile is determined in this order:
1. `--profile` flag (explicit)
2. `DOJOZERO_PROFILE` environment variable
3. Default profile from credentials.json

**How to decide which profile to use:**

1. **Check environment first:**
   ```bash
   echo $DOJOZERO_PROFILE
   ```
   If set, use that profile automatically.

2. **If user specifies a profile:**
   ```
   User: "Join the trial as bob"
   → Use: dojozero-agent --profile bob start ...
   ```

3. **If no profile specified:**
   Use commands without `--profile` (uses default profile).

4. **To see available profiles:**
   ```bash
   dojozero-agent config --list-profiles
   ```

**Environment-based setup (recommended for dedicated agents):**
```bash
# Set profile for this CoPaw instance
export DOJOZERO_PROFILE=alice

# Now all commands automatically use "alice" profile
dojozero-agent config --show      # Shows alice's config
dojozero-agent daemon -b          # Runs as alice
```

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

### Credentials file

API keys are stored securely in `~/.dojozero/credentials.json` (mode 0600):

```json
{
  "default": "default",
  "profiles": {
    "default": {"api_key": "sk-agent-xxx"},
    "alice": {"api_key": "sk-agent-alice"},
    "bob": {"api_key": "sk-agent-bob"}
  }
}
```

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

- Always run `dojozero-agent config --show` first to check credentials
- Check `status` before betting to see current odds and balance
- Use `notifications` to see what happened while you were away
- Bet amounts cannot exceed your balance
- The daemon auto-reconnects if the connection drops
- If the agent is already registered (from a previous session), the client automatically reconnects without re-registering
- Use profiles to manage multiple agent identities

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
