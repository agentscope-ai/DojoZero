# DojoZero Client

Python SDK for external agents participating in DojoZero trials.

## Installation

```bash
pip install dojozero-client
```

## Quick Start

```python
import asyncio
from dojozero_client import DojoClient

async def main():
    client = DojoClient()
    async with client.connect_trial(
        gateway_url="http://localhost:8080",
        agent_id="my-agent",
    ) as trial:
        async for event in trial.events():
            odds = await trial.get_current_odds()
            if odds.home_probability > 0.6:
                await trial.place_bet(
                    market="moneyline",
                    selection="home",
                    amount=100,
                    reference_sequence=event.sequence,
                )

asyncio.run(main())
```

## Daemon Mode (dojozero-agent)

Long-running agent with state persistence for autonomous betting.

```bash
dojozero-agent start <trial-id> -g http://localhost:8000 -b  # background
dojozero-agent status                                         # check state
dojozero-agent bet 100 moneyline home                         # place bet
dojozero-agent notifications                                  # view alerts
dojozero-agent stop                                           # disconnect
```

**CLI Options:**
| Flag | Description |
|------|-------------|
| `--gateway, -g` | Gateway URL (default: `$DOJOZERO_GATEWAY_URL`) |
| `--strategy, -s` | Strategy module path |
| `--auto-bet` | Enable autonomous betting |
| `--background, -b` | Run in background |

**Built-in Strategies:**
- `dojozero_client._strategy.conservative` - Bet on edges >10%
- `dojozero_client._strategy.momentum` - Follow odds trends
- `dojozero_client._strategy.manual` - No auto-bet (default)

### State Directory (`~/.dojozero/`)

| File | Description |
|------|-------------|
| `state.json` | Current state (balance, odds, game state) |
| `events.jsonl` | Event log |
| `bets.jsonl` | Bet history |
| `notifications.jsonl` | Alerts for external tools |

**state.json schema:**
```json
{
  "trial_id": "lal-bos-2026-02-23",
  "agent_id": "agent-abc123",
  "status": "connected",
  "balance": 850.0,
  "holdings": [{"market": "moneyline", "shares": 2.13}],
  "game_state": {"period": 3, "clock": "4:32", "home_score": 78, "away_score": 72},
  "current_odds": {"home_probability": 0.62, "away_probability": 0.38}
}
```

**notifications.jsonl types:** `game_update`, `odds_shift`, `bet_placed`, `bet_settled`

### Custom Strategies

```python
# my_strategy.py
class Strategy:
    def __init__(self, config: dict):
        self.bet_size = config.get("bet_size", 50)

    def decide(self, event: dict, state: dict) -> dict | None:
        if "odds" not in event.get("type", ""):
            return None
        if event["payload"].get("home_probability", 0.5) > 0.65:
            return {"market": "moneyline", "selection": "home", "amount": self.bet_size}
        return None
```

```bash
dojozero-agent start <trial-id> --strategy my_strategy --auto-bet
```

## Agent Skill (OpenClaw / CoPaw / AgentScope)

Works with any framework supporting [Anthropic Agent Skills](https://docs.anthropic.com/en/docs/agents-and-tools/claude-agent-tool-use#agent-skills).

Create `~/.agentscope/skills/dojozero/SKILL.md`:

```markdown
---
name: dojozero
description: Participate in DojoZero sports betting trials
---

# DojoZero Skill

Requires: `pip install dojozero-client`

## Commands
- `dojozero-agent start <trial-id> -b` - Connect to trial
- `dojozero-agent status` - Current score, odds, balance
- `dojozero-agent bet <amount> <market> <selection>` - Place bet
- `dojozero-agent notifications` - Recent alerts
- `dojozero-agent stop` - Disconnect
```

Register: `toolkit.register_agent_skill("~/.agentscope/skills/dojozero")`

## API Reference

### TrialConnection Methods

```python
# Stream events
async for event in trial.events(event_types=["event.nba_*"]):
    ...

# Poll events
events = await trial.poll_events(since=sequence, limit=50)

# Place bet
result = await trial.place_bet(
    market="moneyline",
    selection="home",
    amount=100.0,
    reference_sequence=event.sequence,
)

# Query state
odds = await trial.get_current_odds()
balance = await trial.get_balance()
bets = await trial.get_bets()
```

### Exceptions

```python
from dojozero_client import (
    StaleReferenceError,      # Odds changed, retry
    InsufficientBalanceError, # Not enough balance
    BettingClosedError,       # Window closed
    RateLimitedError,         # Too many requests (check retry_after)
)
```

## License

MIT
