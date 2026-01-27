# Betting Broker (Operator) - Design Overview

## 1. Purpose

The Betting Broker is the central operator that coordinates betting activities between agents and live game events. It manages account balances, processes event updates, handles bet placement and execution, and settles wagers when games conclude.

## 2. Core Responsibilities

1. **Account Management** - Track agent balances, deposits, and withdrawals
2. **Event Management** - Maintain game state and odds throughout event lifecycle
3. **Order Management** - Place, execute, and cancel bet orders (market and limit)
4. **Settlement** - Calculate and distribute payouts when events conclude

## 3. Architecture

```
┌─────────────┐
│ Datastream  │──────┐
└─────────────┘      │
                     │ Event Updates
                     ▼
              ┌──────────────┐
              │    Betting   │
              │    Broker    │
              └──────────────┘
                     ▲
                     │ Bet Requests & Queries
                     │
              ┌──────┴──────┐
              │             │
         ┌────┴───┐    ┌────┴───┐
         │ Agent  │    │ Agent  │
         │   1    │    │   N    │
         └────────┘    └────────┘ 
```

## 3.1. Single Event Model

The broker handles **one event at a time**. `get_event()` returns the current available event (SCHEDULED or LIVE) or `None`. All game context is embedded in tool outputs - agents call `get_event()` first and never provide event_id or team names. Critical parameters like `betting_phase` are required (no defaults) and use `Literal` types to prevent invalid values.

## 4. Event Lifecycle

**Pre-game Phase**
- Event created with initial odds
- Both pre-game market and limit orders accepted
- Odds can be updated, triggering limit order matching

**Game Start Transition**
- Event status changes to LIVE
- Pre-game betting closes
- All unfilled pre-game limit orders are cancelled and refunded

**In-game Phase**
- Only in-game orders accepted (new betting phase)
- Odds continue to update
- Limit orders can still be placed and matched

**Game End Transition**
- Event status changes to CLOSED
- All betting stops immediately
- All unfilled limit orders (both pre-game and in-game) are cancelled and refunded

**Settlement Phase**
- Winner declared
- All active (executed) bets are settled
- Winning bets pay out: `gross_payout = amount × odds`
- Event status changes to SETTLED

## 5. Order Types

**Market Orders**
- Execute immediately at current odds
- Funds locked and bet becomes active instantly
- Synchronous confirmation to agent

**Limit Orders**
- Execute only when odds reach or exceed specified threshold
- Funds locked but bet remains pending
- Added to order book for matching
- Asynchronous notification when executed
- Can be cancelled while pending

## 5.1. Bet Types

The broker supports three bet types:

**Moneyline Betting** (default)
- Bet on which team will win
- Selection: "home" or "away"
- Uses `home_odds` and `away_odds`

**Spread Betting**
- Bet on point spread outcomes
- Selection: "home" or "away" with a spread value (e.g., -3.5)
- Uses `spread_lines` with multiple spread options and their respective odds
- Settlement based on final score adjusted by spread

**Total Betting** (Over/Under)
- Bet on total points scored
- Selection: "over" or "under" with a total value (e.g., 220.5)
- Uses `total_lines` with multiple total options and their respective odds
- Settlement based on combined final score vs. total line

All bet types support both market and limit orders, and odds can be updated dynamically for all types.

## 6. Key Workflows

### Bet Placement
1. Agent calls `get_event()` to get current game context and available betting options
2. Agent sends bet request (no event_id needed - broker uses current event)
3. Broker validates (balance, event status, betting phase match)
4. Funds are locked from agent balance
5. Market orders → immediate execution
6. Limit orders → added to order book, executed when odds match

### Odds Update
1. Datastream sends new odds
2. Broker updates event
3. Broker checks pending limit orders
4. Orders with favorable odds are executed
5. Agents receive async execution notifications

### Event Settlement
1. Datastream sends game result
2. Broker retrieves all active bets for event
3. For each bet: determine outcome, calculate payout
4. Credit winning accounts
5. Mark all bets as settled
6. Send settlement notifications to agents

## 7. Concurrency Model

- **Agent locks**: Ensure atomic operations on individual agent accounts
- **Event locks**: Ensure atomic operations on individual events

## 8. Data Flow

**From Datastream to Broker:**
- StreamEvent with payloads: pregame, odds_update, game_start, game_result

**From Broker to Agents:**
- StreamEvent notifications: bet_executed, bet_settled
- Synchronous responses: bet confirmations ("bet_placed" or "bet_invalid: <reason>"), balance queries, event information (JSON)

**Agent to Broker:**
- Account operations: create_account, deposit, withdraw, get_balance
- Bet operations: place_bet_moneyline, place_bet_spread, place_bet_total, cancel_bet
- Query operations: get_event, get_active_bets, get_pending_orders, get_bet_history, get_statistics

## 9. Agent Tool Configuration

The broker supports configurable tool exposure via `allowed_tools` in the operator configuration.

```yaml
operators:
  - id: betting_broker
    class: BrokerOperator
    initial_balance: "1000.00"
    allowed_tools:
      - get_balance
      - get_event
      - place_bet_moneyline
      - place_bet_spread
      - place_bet_total
      - cancel_bet
      - get_active_bets
      - get_pending_orders
      - get_bet_history
      - get_statistics
```

**Available Tools:**
- `get_balance()` - Get account balance
- `get_event()` - Get current game info and betting options (call first). Returns JSON or "null"
- `place_bet_moneyline(amount, selection, betting_phase, order_type="MARKET", limit_odds=None)` - Bet on winner. `betting_phase` required: "PRE_GAME" for SCHEDULED, "IN_GAME" for LIVE
- `place_bet_spread(amount, selection, spread_value, betting_phase, ...)` - Bet on spread. `spread_value` from `get_event().spread_lines`
- `place_bet_total(amount, selection, total_value, betting_phase, ...)` - Bet over/under. `total_value` from `get_event().total_lines`
- `cancel_bet(bet_id)` - Cancel pending order using bet_id from `get_pending_orders()`
- `get_active_bets()` - Get active bets
- `get_pending_orders()` - Get pending limit orders
- `get_bet_history(limit=20)` - Get settled bet history
- `get_statistics()` - Get performance stats

If `allowed_tools` is omitted or `None`, all tools are enabled by default.

## 10. Logging and Observability

The broker emits logs to SLS (Simple Log Service) whenever account balances or bet statuses change. 

### 10.1. When Logs Are Emitted

Logs are automatically emitted whenever `self._accounts` or `self._bets` are modified:

- **Account Operations**: `account_created`, `deposit`, `withdraw`
- **Bet Operations**: `bet_placed`, `bet_executed`, `bet_settled`, `bet_cancelled`

Each log contains a snapshot of **all** agents' current balances and bet statuses, not just the agent that triggered the change.

### 10.2. Log Format

Logs are emitted as OpenTelemetry spans with the following structure:

**Operation Name:** `broker.state_update`

**Standard Tags:**
- `dojozero.trial.id` - Trial identifier
- `dojozero.actor.id` - Broker actor identifier
- `dojozero.event.type` - `"broker.state_update"`

**Broker-Specific Tags:**
- `broker.change_type` - Type of change that triggered the log (see Change Types below)
- `broker.accounts_count` - Total number of accounts (integer)
- `broker.bets_count` - Total number of bets (integer)
- `broker.accounts` - JSON string containing all account balances (serialized via Pydantic TypeAdapter)
- `broker.bets` - JSON string containing all bets keyed by bet_id (serialized via Pydantic model_dump)

### 10.3. Change Types

The `broker.change_type` tag indicates what operation triggered the log:

| Change Type | Description | When Emitted |
|------------|-------------|--------------|
| `account_created` | New agent account created | `create_account()` called |
| `deposit` | Funds added to account | `deposit()` called |
| `withdraw` | Funds removed from account | `withdraw()` called |
| `bet_placed` | New bet placed (funds locked) | `place_bet()` called |
| `bet_executed` | Limit order executed | `_match_bet()` called (limit order filled) |
| `bet_settled` | Bet settled with outcome | `_settle_bet()` called (game ended) |
| `bet_cancelled` | Pending order cancelled | `_cancel_pending_order()` called |

### 10.4. Data Structure

#### Accounts Data (`broker.accounts`)

JSON string containing a map of agent IDs to account information:

```json
{
  "agent1": {
    "balance": "1000.00",
    "last_updated": "2024-01-01T12:00:00"
  },
  "agent2": {
    "balance": "500.00",
    "last_updated": "2024-01-01T12:05:00"
  }
}
```

#### Bets Data (`broker.bets`)

JSON string containing a flat map of bet IDs to complete bet information. All bets are serialized using Pydantic's `model_dump(mode="json")`, which provides full type safety and automatic serialization of all bet fields.

```json
{
  "bet-1": {
    "bet_id": "bet-1",
    "agent_id": "agent1",
    "event_id": "event-1",
    "amount": "100.00",
    "selection": "home",
    "odds": "1.85",
    "order_type": "MARKET",
    "limit_odds": null,
    "betting_phase": "PRE_GAME",
    "create_time": "2024-01-01T12:00:00",
    "execution_time": "2024-01-01T12:00:01",
    "status": "ACTIVE",
    "bet_type": "MONEYLINE",
    "spread_value": null,
    "total_value": null,
    "actual_payout": null,
    "outcome": null,
    "settlement_time": null
  },
  "bet-3": {
    "bet_id": "bet-3",
    "agent_id": "agent1",
    "event_id": "event-1",
    "amount": "200.00",
    "selection": "home",
    "odds": "2.00",
    "order_type": "LIMIT",
    "limit_odds": "2.00",
    "betting_phase": "PRE_GAME",
    "create_time": "2024-01-01T12:05:00",
    "execution_time": null,
    "status": "PENDING",
    "bet_type": "MONEYLINE",
    "spread_value": null,
    "total_value": null,
    "actual_payout": null,
    "outcome": null,
    "settlement_time": null
  }
}
```



**All Fields:**
- `bet_id` - Unique bet identifier
- `agent_id` - Agent who placed the bet
- `event_id` - Event identifier
- `amount` - Bet amount (as string, Decimal serialized)
- `selection` - Bet selection ("home", "away", "over", "under")
- `odds` - Execution odds (Decimal serialized as string)
- `order_type` - "MARKET" or "LIMIT"
- `limit_odds` - Limit order threshold (null for market orders)
- `betting_phase` - "PRE_GAME" or "IN_GAME"
- `create_time` - When bet was created (ISO format)
- `execution_time` - When bet was executed (null if pending)
- `status` - Current bet status ("ACTIVE", "PENDING", "SETTLED", or "CANCELLED")
- `bet_type` - Type of bet ("MONEYLINE", "SPREAD", or "TOTAL")
- `spread_value` - Spread value for SPREAD bets (null for other bet types)
- `total_value` - Total value for TOTAL bets (null for other bet types)
- `actual_payout` - Payout amount (null until settled)
- `outcome` - "WIN" or "LOSS" (null until settled)
- `settlement_time` - When bet was settled (null until settled)
