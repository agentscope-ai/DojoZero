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

## 6. Key Workflows

### Bet Placement
1. Agent sends bet request
2. Broker validates (balance, event status, betting phase)
3. Funds are locked from agent balance
4. Market orders → immediate execution
5. Limit orders → added to order book, executed when odds match

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
- Synchronous responses: bet confirmations, balance queries, quote requests

**Agent to Broker:**
- Account operations: create_account, deposit, withdraw, get_balance
- Bet operations: place_bet, cancel_bet
- Query operations: get_quote, get_active_bets, get_pending_orders, get_bet_history, get_statistics
