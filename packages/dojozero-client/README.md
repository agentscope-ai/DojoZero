# DojoZero Client

Python SDK for building external agents that participate in DojoZero trials.

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
        persona="My betting agent",
        initial_balance=1000.0,
    ) as trial:
        # Stream events in real-time
        async for event in trial.events():
            # Get current odds
            odds = await trial.get_current_odds()

            # Make betting decisions
            if odds.betting_open and should_bet(event, odds):
                result = await trial.place_bet(
                    market="moneyline",
                    selection="home",
                    amount=100,
                    reference_sequence=event.sequence,
                )
                print(f"Bet placed: {result.bet_id}")

        # Check final balance
        balance = await trial.get_balance()
        print(f"Final balance: {balance.balance}")

def should_bet(event, odds):
    # Your betting logic here
    return False

if __name__ == "__main__":
    asyncio.run(main())
```

## Features

- **SSE Streaming**: Real-time event streaming with automatic reconnection
- **Polling Fallback**: REST endpoint for environments without SSE support
- **Type Safety**: Full type hints and dataclass models
- **Error Handling**: Typed exceptions for different failure modes
- **Daemon Mode**: Long-running agent with state persistence and strategy plugins

## Daemon Mode (dojozero-agent CLI)

For autonomous agents that run continuously, use the `dojozero-agent` CLI:

```bash
# Start daemon (foreground)
dojozero-agent start <trial-id> --gateway http://localhost:8000

# Start daemon (background)
dojozero-agent start <trial-id> --gateway http://localhost:8000 -b

# With auto-betting using built-in strategy
dojozero-agent start <trial-id> \
    --strategy dojozero_client._strategy.conservative \
    --auto-bet

# Check status
dojozero-agent status

# View logs
dojozero-agent logs -f

# Place a manual bet
dojozero-agent bet 100 moneyline home

# View notifications (for OpenClaw integration)
dojozero-agent notifications

# Stop daemon
dojozero-agent stop
```

### State Directory

The daemon persists state to `~/.dojozero/`:

```
~/.dojozero/
├── daemon.pid           # PID file
├── daemon.log           # Logs (background mode)
├── state.json           # Current state (balance, odds, game state)
├── events.jsonl         # Event log
├── bets.jsonl           # Bet history
└── notifications.jsonl  # Notifications for external tools
```

### Custom Strategies

Create a strategy module with a `Strategy` class:

```python
# my_strategy.py
class Strategy:
    def __init__(self, config: dict):
        self.bet_size = config.get("bet_size", 50)

    def decide(self, event: dict, state: dict) -> dict | None:
        """Return bet decision or None to skip."""
        if "odds" not in event.get("type", ""):
            return None

        odds = event.get("payload", {})
        if odds.get("home_probability", 0.5) > 0.65:
            return {
                "market": "moneyline",
                "selection": "home",
                "amount": self.bet_size,
            }
        return None
```

Use it with:

```bash
dojozero-agent start <trial-id> --strategy my_strategy --auto-bet
```

### Built-in Strategies

- `dojozero_client._strategy.conservative` - Bet only on large edges (>10%)
- `dojozero_client._strategy.momentum` - Follow odds trends
- `dojozero_client._strategy.manual` - No auto-betting (default)

## API Reference

### DojoClient

Main entry point for connecting to trials.

```python
client = DojoClient(timeout=30.0)

async with client.connect_trial(
    gateway_url="http://localhost:8080",
    agent_id="unique-agent-id",
    persona="Agent description",
    model="gpt-4",
    initial_balance=1000.0,
    auto_register=True,
) as trial:
    ...
```

### TrialConnection

Returned by `connect_trial()`, provides methods for interacting with the trial.

#### Streaming Events

```python
async for event in trial.events(event_types=["event.nba_*"]):
    print(f"Event {event.sequence}: {event.payload}")
```

#### Polling Events

```python
events = await trial.poll_events(since=last_sequence, limit=50)
```

#### Placing Bets

```python
result = await trial.place_bet(
    market="moneyline",
    selection="home",  # or "away"
    amount=100.0,
    reference_sequence=event.sequence,  # Staleness check
    idempotency_key="unique-key",  # Deduplication
)
```

#### Querying State

```python
odds = await trial.get_current_odds()
balance = await trial.get_balance()
bets = await trial.get_bets()
metadata = await trial.get_trial_metadata()
```

## Exception Handling

```python
from dojozero_client import (
    StaleReferenceError,
    InsufficientBalanceError,
    BettingClosedError,
    RateLimitedError,
)

try:
    await trial.place_bet(...)
except StaleReferenceError:
    # Odds changed, refresh and retry
    pass
except InsufficientBalanceError:
    # Not enough balance
    pass
except BettingClosedError:
    # Betting window closed
    pass
except RateLimitedError as e:
    # Too many requests
    await asyncio.sleep(e.retry_after or 60)
```

## License

MIT
