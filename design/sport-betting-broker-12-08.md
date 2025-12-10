## Broker (Operator)

### 1. Overview
The Betting Broker (Operator) performs four core functions: 
1. Managing account balances
2. **Managing event (streamEvent driven)**: pregame, odds_update, game_started, game_ended, game_result
3. Placing and **filling** bets
4. Settling wagers

### 2. Component Interaction Flow
```
┌─────────────┐
│ Datastream  │──────┐
└─────────────┘      │
                     │ Event Data (Game Status, Odds Updates, Results)
                     ▼
              ┌──────────────┐
              │    Betting   │
              │    Broker    │
              └──────────────┘
                     ▲
                     │ Bet Requests (place, cancel), Quotes
                     │ Balance Queries
                     │
              ┌──────┴──────┐
              │             │
         ┌────┴───┐    ┌────┴───┐
         │ Agent  │    │ Agent  │
         │   1    │    │   N    │
         └────────┘    └────────┘ 
```
## Event Lifecycle Workflow

**Pre-game Phase (Betting Open)**
```
Datastream → Broker: EVENT_CREATED (pregame payload)
Broker: initialize_event()
  → Creates event with initial odds  # Opens pre-game betting

Datastream → Broker: ODDS_UPDATE
Broker: update_odds()
  → Updates pre-game odds
```

**In-game Phase (Live Betting)**
```
Datastream → Broker: GAME_STARTED
Broker: update_event_state()
  → Changes the state of the event (Rejects pre-game betting)
  → Clears unfilled pre-game orders in the order book

Datastream → Broker: ODDS_UPDATE
Broker: update_odds()
  → Updates live odds during game
```

**Settlement Phase**
```
Datastream → Broker: EVENT_RESULT
Broker: update_event_state(), settle_event()
  → Changes the state of the event (Rejects all betting)
  → Clears all unfilled orders in the order book
  → Settles all filled bets
```



---

## 3. Data Models

**Account**
```python
Account {
    agent_id: String
    balance: Decimal
    created_at: Timestamp
    last_updated: Timestamp
}
```

**Event**
```python
Event {
    event_id: String
    home_team: String
    away_team: String
    game_time: Timestamp
    status: Enum["SCHEDULED", "LIVE", "CLOSED", "SETTLED"]
    home_odds: Decimal
    away_odds: Decimal
    last_odds_update: Timestamp
    betting_closed_at: Timestamp (null if still open)
}
```

**Bet Order Types**
```python
OrderType = Enum["MARKET", "LIMIT"]
BettingPhase = Enum["PRE_GAME", "IN_GAME"]

BetRequest {
    amount: Decimal
    selection: String  # "home" or "away"
    event_id: String
    order_type: OrderType
    betting_phase: BettingPhase
    limit_odds: Decimal (required if order_type == "LIMIT")
}
```

**Bet**
```python
Bet {
    bet_id: String
    agent_id: String
    event_id: String
    amount: Decimal
    selection: String
    odds: Decimal  # actual odds at execution
    order_type: OrderType
    limit_odds: Decimal # null for market orders
    betting_phase: BettingPhase
    create_time: Timestamp
    execution_time: Timestamp # The time that this bet is actually executed.
    status: Enum["PENDING", "ACTIVE", "SETTLED", "CANCELLED"]
    actual_payout: Decimal # null if not settled
    outcome: Enum["WIN", "LOSS", null]
    settlement_time: Timestamp # null if not settled
}
```

**StreamEvent Structure**

All events from the datastream are wrapped in a `StreamEvent` object:
```python
@dataclass
class StreamEvent(Generic[PayloadT]):
    """Envelope for data emitted by a :class:`DataStream`."""

    stream_id: str  # Actor ID of the producer of the payload.
    payload: PayloadT
    emitted_at: datetime = field(default_factory=_utcnow)
    sequence: int | None = None
    metadata: JSONDict = field(default_factory=dict)

```

**Payload Types**

*Pregame Event Payload (initializes odds)*
```python

@dataclass
class PregamePayload:
    event_id: str
    home_team: str
    away_team: str
    game_time: str
    initial_home_odds: float
    initial_away_odds: float
```

*Odds Update Event Payload*
```python
@dataclass
class OddsUpdatePayload:
    event_id: str
    home_odds: float
    away_odds: float
```

*Game Start Event Payload*
```python
@dataclass
class GameStartPayload:
    event_id: str
```

*Game Result Event Payload*
```python
@dataclass
class GameResultPayload:
    event_id: str
    winner: str  # "home" or "away"
    final_score: dict[str, int]  # e.g., {"home": 108, "away": 102}
```

---

### 4. Broker Internal State
```python
BrokerOperator {
    # Account management
    _accounts: Dict[str, Account]
    _agent_locks: Dict[str, asyncio.Lock]
    _event_locks: Dict[str, asyncio.Lock]
    # Event management
    _events: Dict[str, Event]  # event_id -> Event (includes current odds)

    # Bet management
    _bets: Dict[str, Bet]  # bet_id -> Bet

    # Agent-indexed bet tracking
    _active_bets: Dict[str, List[str]]  # agent_id -> [bet_ids]
    _pending_orders: Dict[str, List[str]]  # agent_id -> [bet_ids]
    _bet_history: Dict[str, List[str]]  # agent_id -> [bet_ids]

    # Event-indexed bet tracking
    _event_active_bets: Dict[str, Set[str]]  # event_id -> {bet_ids} (active bets, for settlement)
    _event_pending_orders: Dict[str, Set[str]]  # AKA: order book: event_id -> {bet_ids} (pending limit orders, for matching)

}
```
---

## 5. Core Functions

### 5.1 Event Stream Processing
(`handle_stream_event`, `initialize_event`, `update_odds`, `update_event_status`, `settle_event`)


```python
async def handle_stream_event(
    self,
    event: StreamEvent[Any]
) -> None:
    """Process incoming stream events and delegate to appropriate handlers.
    
    Event type routing:
        - "pregame" → initialize_event()
        - "odds_update" → update_odds()
        - "game_start" → update_event_status(status="LIVE")
        - "game_result" → update_event_status(status="CLOSED"), settle_event()
    
    Side Effects:
        Varies by event type (see individual handlers below)
    """
    payload = event.payload
    event_id = payload.get("event_id")
    async with self._event_locks[event_id]: # apply the event lock
        event_type = payload.get("type")
        if event_type == "pregame":
            await self.initialize_event(
                event_id=payload["event_id"],
                home_team=payload["home_team"],
                away_team=payload["away_team"],
                game_time=datetime.fromisoformat(payload["game_time"]),
                initial_home_odds=Decimal(str(payload["initial_home_odds"])),
                initial_away_odds=Decimal(str(payload["initial_away_odds"]))
            )
        elif event_type == "odds_update":
            await self.update_odds(
                event_id=payload["event_id"],
                home_odds=Decimal(str(payload["home_odds"])),
                away_odds=Decimal(str(payload["away_odds"]))
            )
        elif event_type == "game_start":
            await self.update_event_status(
                event_id=payload["event_id"],
                status="LIVE"
            )
        elif event_type == "game_result": # merge the game end logic here
            await self.update_event_status(
                event_id=payload["event_id"],
                status="CLOSED"
            )
            await self.settle_event(
                event_id=payload["event_id"],
                winner=payload["winner"],
                final_score=payload["final_score"]
            )
        ...

async def initialize_event(
    self,
    event_id: str,
    home_team: str,
    away_team: str,
    game_time: Timestamp,
    initial_home_odds: Decimal,
    initial_away_odds: Decimal
) -> Event:
    """Initialize a new betting event with starting odds.
    
    Raises:
        ValueError: If event_id already exists
        ValueError: If odds are invalid (≤ 1.0)
    
    Side Effects:
        - Creates event record with status "SCHEDULED"
        - Opens event for betting (both market and limit orders)
        - Logs event creation
    
    Returns:
        Created Event object
    """
    ...

async def update_odds(
    self,
    event_id: str,
    home_odds: Decimal,
    away_odds: Decimal
) -> Event:
    """Update odds for an event and execute matching limit orders.
    
    Workflow:
        1. Validate event exists and is bettable (SCHEDULED or LIVE)
        2. Update event odds
        3. Check all pending limit orders for this event
        4. Execute any limit orders where:
           - home limit orders: current home_odds >= limit_odds
           - away limit orders: current away_odds >= limit_odds
        5. Send async execution notifications to affected agents
    
    Raises:
        ValueError: If event not found
        ValueError: If event is CLOSED or SETTLED
        ValueError: If odds are invalid (≤ 1.0)
    
    Side Effects:
        - Updates event odds
        - Updates last_odds_update timestamp
        - Executes matching limit orders via match_bet()
        - Logs odds change
    
    Returns:
        Updated Event object
    """
    ...

async def update_event_status(
    self,
    event_id: str,
    status: str
) -> None:
    """Update event status and perform status-specific actions.
    
    Status transitions:
        SCHEDULED → LIVE (game_start):
            - No new prebets accepted after this
            - Clears all unfilled pregame orders
        
        LIVE → CLOSED (game_end):
            - Locks all betting activity
            - Clears all unfilled pending orders
            - Refunds locked funds to agents
            - Prepares for settlement
    
    Args:
        event_id: The event to update
        status: New status ("LIVE" or "CLOSED")
    
    Raises:
        ValueError: If event not found
        ValueError: If status transition is invalid
    
    Side Effects:
        - Updates event status
        - Records betting_closed_at timestamp (for LIVE)
        - Cancels pending orders (for CLOSED)
        - Refunds locked funds (for CLOSED)
        - Logs status change
    """
    ...

async def settle_event(
    self,
    event_id: str,
    winner: str,
    final_score: Dict[str, int]
) -> None:
    """Settle all active bets for a completed event.
    
    Workflow:
        1. Validate event is CLOSED
        2. Retrieve all active bets for event
        3. For each bet:
           - Determine if won or lost
           - Calculate payout if won: gross_payout = amount × odds
           - Credit winning accounts
           - Update bet status to SETTLED
        4. Update event status to SETTLED
        5. Send settlement notifications to all agents
        6. Move all bets to history
    
    Args:
        event_id: The event to settle
        winner: Winning side ("home" or "away")
        final_score: Final score dict, e.g., {"home": 108, "away": 102}
    
    Raises:
        ValueError: If event not found
        ValueError: If event status is not CLOSED
        ValueError: If winner is invalid
    
    Side Effects:
        - Credits winning accounts
        - Updates all bet records (status, outcome, payout, settlement_time)
        - Changes event status to SETTLED
        - Moves bets from active to history
        - Sends StreamEvent notifications to agents
        - Logs settlement details
    """
    ...
```


### 5.2 Account Management
(`create_account`, `get_balance`, `deposit`, `withdraw`)
```python
async def create_account(
    self, 
    agent_id: str, 
    initial_balance: Decimal
) -> Account:
    """Initialize a new agent account.

    Raises:
        ValueError: If agent_id already exists
        ValueError: If initial_balance is negative
    """
    ...

async def get_balance(
    self, 
    agent_id: str
) -> Decimal:
    """Retrieve current account balance.

    Raises:
        ValueError: If account not found
    """
    ...

async def deposit(
    self, 
    agent_id: str, 
    amount: Decimal
) -> Decimal:
    """Add funds to agent account.

    Raises:
        ValueError: If amount is not positive
        ValueError: If account not found
    
    Side Effects:
        - Increases account balance
        - Updates last_updated timestamp
        - Logs transaction
    """
    ...

async def withdraw(
    self, 
    agent_id: str, 
    amount: Decimal
) -> Decimal:
    """Remove funds from agent account.
    
    Raises:
        ValueError: If amount exceeds balance
        ValueError: If amount is not positive
        ValueError: If account not found
    
    Side Effects:
        - Decreases account balance
        - Updates last_updated timestamp
        - Logs transaction
    """
    ...
```

---

### 5.3 Bet Management
(`get_quote`, `place_bet`, `match_bet`, `cancel_bet`)

```python
async def get_quote(self, event_id: str) -> Dict[str, Any]:
    """Raises ValueError if event not found
    Returns:
        Dictionary with event details:
        {
            "event_id": str,
            "home_team": str,
            "away_team": str,
            "game_time": str (ISO format),
            "status": str,  # "SCHEDULED", "LIVE", "CLOSED", "SETTLED"
            "home_odds": str (Decimal as string),
            "away_odds": str (Decimal as string),
            "last_odds_update": str (ISO format),
            "betting_closed_at": str (ISO format) or None
        }

    """
    async with self._event_locks[bet_request.event_id]:
        if event_id not in self._events:
            raise ValueError(f"Event {event_id} not found")
        return self._events[event_id].to_dict()


async def place_bet(
    self,
    agent_id: str,
    bet_request: BetRequest
) -> str:
    async with self._agent_locks[agent_id]:
    """Accept and validate a bet request (synchronous confirmation).
    
    Workflow:
        1. Validate bet request
        2. Check event exists and is accepting bets
        3. Verify account has sufficient balance
        4. Lock funds from account
        5. Generate unique bet_id
        6. Create bet record with status based on order type:
           - MARKET orders: immediately call match_bet()
           - LIMIT orders: add to pending orders (order book)
        7. Return bet confirmation to agent (synchronous)
    
    Note: This function confirms the bet is PLACED, not necessarily EXECUTED.
          Actual execution happens in match_bet() which sends async notification.
    
    
    Raises:
        ValueError: If account not found or insufficient balance
        ValueError: If event not found
        ValueError: If event not accepting bets (wrong status)
        ValueError: If betting_phase doesn't match event status
        ValueError: If limit_odds not provided for LIMIT orders
        ValueError: If bet amount is not positive
    
    Returns:
        Status message (string):
        - "bet_placed" - Bet successfully placed (funds locked)
        - "bet_invalid" - Bet rejected due to validation error

    Side Effects:
        - Decreases account balance (locks funds)
        - For MARKET orders: immediately executes via match_bet()
        - For LIMIT orders: adds to _pending_orders and _event_pending_orders
        - Creates audit log entry
    """
    ...

async def match_bet(
    self,
    bet: Bet,
    execution_odds: Decimal
) -> None:
    """Execute a bet at specified odds (asynchronous notification).

    Workflow:
        1. Update bet record:
           - Set odds = execution_odds
           - Set execution_time = now
           - Set status = "ACTIVE"
        2. Move bet from pending to active:
           - Remove from _pending_orders (if exists - for LIMIT orders)
           - Remove from _event_pending_orders (if exists - for LIMIT orders)
           - Add to _active_bets (always)
           - Add to _event_active_bets (always)
        3. Send StreamEvent notification to agent:
           {
               "type": "bet_executed",
               "bet_id": str,
               "agent_id": str,
               "event_id": str,
               "selection": str,
               "amount": Decimal,
               "execution_odds": Decimal,
               "execution_time": str (ISO timestamp)
           }
        4. Log execution

    Side Effects:
        - Updates bet record
        - Moves bet from pending to active collections
        - Sends async StreamEvent to agent
        - Creates execution log

    Note: 
        - For MARKET orders: Called immediately from place_bet()
          Bet is never in pending collections, so removal is a no-op.
        - For LIMIT orders: Called from update_odds() when odds match
          Bet must be removed from pending collections.
    """
    ...

async def cancel_bet(
    self,
    agent_id: str,
    bet_id: str
) -> str:
    async with self._agent_locks[agent_id]:
    """Cancel a pending limit order and refund locked funds.
    
    Workflow:
        1. Validate bet exists and belongs to agent
        2. Verify bet is in pending status (not executed)
        3. Remove from pending orders collections
        4. Refund locked amount to account
        5. Mark bet as cancelled
        6. Log cancellation
    
    Raises:
        ValueError: If bet not found
        ValueError: If bet doesn't belong to agent
        ValueError: If bet is already executed or settled
    
    Returns:
        Status message (string):
        - "bet_cancelled" - Bet successfully cancelled and funds refunded
        - "cancel_failed" - Cancellation failed (see logs for reason)
    
    Side Effects:
        - Increases account balance (refund)
        - Removes from _pending_orders
        - Removes from _event_pending_orders
        - Updates bet status to "CANCELLED"
        - Logs cancellation
    
    Note: Only pending LIMIT orders can be cancelled.
          MARKET orders execute immediately and cannot be cancelled.
    """
    ...
```
---

### 5.4 Query Functions
(`get_active_bets`, `get_pending_orders`, `get_bet_history`, `get_statistics`, `get_available_events`)

```python
async def get_active_bets(
    self,
    agent_id: str
) -> List[Bet]:
    """Retrieve all active bets (executed, not settled).
    
    Returns:
        List of active Bet objects for the agent
    """
    ...

async def get_pending_orders(
    self,
    agent_id: str
) -> List[Bet]:
    """Retrieve all pending limit orders (not yet executed).
    
    Returns:
        List of pending Bet objects for the agent
    """
    ...

async def get_bet_history(
    self,
    agent_id: str,
    limit: int = 100
) -> List[Bet]:
    """Retrieve settled bet history.
    
    Returns:
        List of Bet objects (most recent first), up to limit
    """
    ...

async def get_statistics(
    self,
    agent_id: str
) -> dict:
    """Calculate performance metrics for an agent.
    
    Returns:
        {
            "total_bets": int,
            "total_wagered": Decimal,
            "wins": int,
            "losses": int,
            "win_rate": float,  # wins / total_bets
            "net_profit": Decimal,  # total_won - total_lost
            "roi": float  # net_profit / total_wagered
        }
    """
    ...

async def get_available_events(
    self
) -> List[Event]:
    """Get all events currently accepting bets.
    
    Returns:
        List of Event objects with status "SCHEDULED"
    """
    ...
```
