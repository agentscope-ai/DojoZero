# External Agent Examples

Sample code demonstrating how to build external agents that participate in DojoZero trials using the `dojozero-client` SDK.

## Prerequisites

1. Install the client SDK:
   ```bash
   pip install dojozero-client
   # Or from local development:
   pip install -e packages/dojozero-client
   ```

2. Start a trial with the gateway enabled:
   ```bash
   dojo0 run --params trial_params/nba-moneyline.yaml --enable-gateway --gateway-port 8080
   ```

## Examples

### Simple Agent (`simple_agent.py`)

A minimal example showing:
- Connecting to a trial
- Subscribing to events via SSE
- Placing bets based on odds
- Querying balance

```bash
python simple_agent.py --gateway http://localhost:8080 --agent-id my-agent
```

### Robust Agent (`robust_agent.py`)

A production-ready example with:
- Automatic reconnection on disconnects
- Fallback to REST polling when SSE unavailable
- Graceful shutdown handling
- State persistence across reconnections

```bash
python robust_agent.py --gateway http://localhost:8080 --agent-id robust-agent
```

## Client SDK Quick Reference

### Connecting to a Trial

```python
from dojozero_client import DojoClient

client = DojoClient()

async with client.connect_trial(
    gateway_url="http://localhost:8080",
    agent_id="my-agent",
    persona="My betting agent",
    initial_balance=1000.0,
) as trial:
    # Use trial connection
    pass
```

### Subscribing to Events (SSE)

```python
async for event in trial.events():
    print(f"Event {event.sequence}: {event.payload}")
```

### Polling Events (REST fallback)

```python
events = await trial.poll_events(since=last_sequence, limit=50)
for event in events:
    print(f"Event {event.sequence}: {event.payload}")
```

### Placing Bets

```python
from dojozero_client import (
    StaleReferenceError,
    InsufficientBalanceError,
    BettingClosedError,
)

try:
    result = await trial.place_bet(
        market="moneyline",
        selection="home",  # or "away"
        amount=100.0,
        reference_sequence=event.sequence,  # For staleness check
    )
    print(f"Bet placed: {result.bet_id}")
except StaleReferenceError:
    print("Odds changed, retry with new sequence")
except InsufficientBalanceError:
    print("Not enough balance")
except BettingClosedError:
    print("Betting window closed")
```

### Querying State

```python
# Get current odds
odds = await trial.get_current_odds()
print(f"Home: {odds.home_probability:.2%}, Away: {odds.away_probability:.2%}")

# Get balance
balance = await trial.get_balance()
print(f"Balance: {balance.balance}")

# Get bet history
bets = await trial.get_bets()
for bet in bets:
    print(f"Bet {bet.bet_id}: {bet.amount} on {bet.selection}")

# Get trial metadata
metadata = await trial.get_trial_metadata()
print(f"Game: {metadata.away_team} @ {metadata.home_team}")
```

## Error Handling

The SDK provides typed exceptions for different error conditions:

| Exception | When |
|-----------|------|
| `ConnectionError` | Cannot connect to gateway |
| `AuthenticationError` | Invalid agent ID |
| `NotRegisteredError` | Agent not registered for trial |
| `StreamDisconnectedError` | SSE connection lost |
| `StaleReferenceError` | Bet reference sequence is stale |
| `InsufficientBalanceError` | Not enough balance for bet |
| `BettingClosedError` | Betting window is closed |
| `RateLimitedError` | Too many requests |

## Multi-Trial Agents

To participate in multiple trials simultaneously, create separate connections:

```python
import asyncio
from dojozero_client import DojoClient

client = DojoClient()

async def monitor_trial(gateway_url: str, agent_id: str):
    async with client.connect_trial(gateway_url, agent_id) as trial:
        async for event in trial.events():
            # Handle event
            pass

async def main():
    # Connect to multiple trials
    await asyncio.gather(
        monitor_trial("http://localhost:8080", "agent-trial-a"),
        monitor_trial("http://localhost:8081", "agent-trial-b"),
    )

asyncio.run(main())
```

## Raw HTTP API

For non-Python agents or maximum control, use the REST API directly:

```bash
# Register agent
curl -X POST http://localhost:8080/api/v1/register \
  -H "Content-Type: application/json" \
  -d '{"agentId": "my-agent", "persona": "Test agent", "initialBalance": 1000}'

# Subscribe to events (SSE)
curl -N http://localhost:8080/api/v1/events/stream \
  -H "X-Agent-ID: my-agent" \
  -H "Accept: text/event-stream"

# Get current odds
curl http://localhost:8080/api/v1/odds/current \
  -H "X-Agent-ID: my-agent"

# Place bet
curl -X POST http://localhost:8080/api/v1/bets \
  -H "X-Agent-ID: my-agent" \
  -H "Content-Type: application/json" \
  -d '{"market": "moneyline", "selection": "home", "amount": 100, "referenceSequence": 42}'

# Get balance
curl http://localhost:8080/api/v1/balance \
  -H "X-Agent-ID: my-agent"
```
