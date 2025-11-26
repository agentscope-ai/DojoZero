## Broker (Operator)

### 1. Overview
The Betting Broker (Operator) performs three core functions: (1) managing account balances, (2) placing bets, and (3) settling wagers.


### 2 Component Interaction Flow
```
┌─────────────┐
│ Datastream  │──────┐
└─────────────┘      │
                     │ Event Data
                     ▼
              ┌──────────────┐
              │    Betting   │
              │    Broker    │
              └──────────────┘
                     ▲
                     │ Bet Requests
                     │ & Queries
                     │
              ┌──────┴──────┐
              │             │
         ┌────┴───┐    ┌────┴───┐
         │ Agent  │    │ Agent  │
         │   1    │    │   N    │
         └────────┘    └────────┘ 
```

**Workflow of bet placement**
```
Agent → Broker: place_bet()
Broker: validate_bet()
Broker: lock_funds()
Broker → Agent: bet_confirmation
Datastream → Broker: event_result
Broker: settle_bet()
Broker → Agent: settlement_notification
```

---

## 3. Data Models

**Account**
```
Account {
    agent_id: String
    balance: Float
    created_at: Timestamp
    last_updated: Timestamp
}
```

**Bet**
```
Bet {
    bet_id: String
    agent_id: String
    event_id: String
    amount: Float
    selection: String
    odds: Float
    create_time: Timestamp
    status: Enum["ACTIVE", "SETTLED"]
    actual_payout: Float (null if not settled)
    outcome: Enum["WIN", "LOSS", null]
    settlement_time: Timestamp (null if not settled)
}
```

**Bet Request (agent -> broker)**
```
BetRequest {
    amount: Float
    selection: String
    odds: Float
    event_id: String
}
```

**Event Result (datastream -> broker)**
```
EventResult {
    event_id: String
    final_data: Object
    timestamp: Timestamp
}
```

---

## 4. Core Functions

**4.1 Account management** (`create_account`, `get_balance`, `deposit`, `withdraw`)

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

**4.2 Bet Management** (`place_bet`, `settle_bet`)

```python
async def place_bet(
    self,
    agent_id: str,
    bet_request: BetRequest
) -> Bet:
    """Accept and process a new bet from an agent.
    
    Workflow:
        1. Validate bet request
        2. Lock funds from account
        3. Generate unique bet ID
        4. Create bet record
        5. Store in active bets
        6. Notify agent [BET_PLACED, BET_REJECTED]
        7. Log bet placement
    
    Raises:
        ValueError: If account not found or insufficient balance
        ValueError: If odds are invalid (≤ 1.0)
        ValueError: If event is closed for betting
    
    Side Effects:
        - Decreases account balance
        - Adds to active_bets collection
        - Sends notification to agent
        - Creates audit log entry
    """
    ...

async def settle_bet(
    self,
    bet: Bet,
    result: EventResult
) -> None:
    """Resolve a bet based on event outcome.
    
    Workflow:
        1. Evaluate bet against result
        2. Calculate payout (if win): 
           gross_payout = bet.amount × bet.odds
        3. Credit account (if win)
        4. Update bet status
        5. Notify agent [BET_WON, BET_LOST]
        6. Log settlement
    
    Side Effects:
        - Updates account balance (if win)
        - Sends win/loss notification
        - Creates settlement log
    """
    ...
```
---
**4.3 Query Functions** (`get_active_bets`, `get_bet_history`, `get_statistics`)
```python
async def get_active_bets(
    self,
    agent_id: str
) -> list[Bet]:
    """Retrieve all active bets for an agent.
    
    Returns:
        List of active Bet objects for the agent
    """
    ...


async def get_bet_history(
    self,
    agent_id: str,
    limit: int = 100  # max records to return
) -> list[Bet]:
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
```

---
## TODO

1. logging: what to log? transaction logs, bet logs, settlement_log
2. concurrency control? One lock per agent for account balance modification operations (`withdraw`, `deposit`, `place_bet`, `settle_bet`)
`_agent_lock(agent_id: str)`