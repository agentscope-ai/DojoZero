# Data Infrastructure Types

This module provides `DataFact` and `DataEvent` base classes and their implementations for the NBA betting data infrastructure.

## Core Concept: Fact = Snapshot, Event = Update

**Fundamental Relationship**:
- **Fact** = **Snapshot** (current state at a point in time)
- **Event** = **Update** (change/delta that contributes to building the snapshot)
- **Multiple Events** → **Aggregate** → **Fact**
- **Fact** = Initial State + Sum of All Events

**Example**:
```
Event 1: PlayByPlayEvent (+2 points) → Score: 2-0
Event 2: PlayByPlayEvent (+3 points) → Score: 2-3  
Event 3: PlayByPlayEvent (+2 points) → Score: 4-3
...
Event N: PlayByPlayEvent (+1 point)  → Score: 45-42

Fact: GameScoreFact (snapshot) → Current Score = 45-42
```

The aggregator (`PlayByPlayToScoreAggregator`) processes events sequentially and maintains the current fact (snapshot).

## DataFact vs DataEvent

### DataFact (Pull-Based State Snapshots)

**Purpose**: Represent current state at a point in time

**Characteristics**:
- ✅ Optimized for fast, one-time queries
- ✅ Synchronous/blocking access pattern
- ✅ Cached/materialized from events
- ✅ No "before" state (just current state)
- ✅ Simple state snapshots

**Class Variables**:
- `fact_type: str` - Type identifier
- `timestamp: datetime` - When fact was captured
- `game_id: str | None` - Optional game identifier
- `metadata: JSONDict` - Additional context

**Methods**:
- `to_dict() -> dict` - Serialize to dictionary
- `from_dict(data: dict) -> DataFact` - Deserialize from dictionary

**Child Classes**:
- `GameScoreFact` - Current game score
- `OddsFact` - Current betting odds
- `GameStatusFact` - Current game status
- `TeamStatsFact` - Current team statistics
- `PlayerStatusFact` - Current player status

### DataEvent (Push-Based Change Events)

**Purpose**: Represent updates/changes in the system

**Characteristics**:
- ✅ Optimized for streaming/push-based access
- ✅ Asynchronous event-driven behavior
- ✅ Source of truth (facts derived from events)
- ✅ Can be incremental (delta) or snapshot (full state)
- ✅ Used for reactive/event-driven agent behavior

**Update Types**:
- **Incremental** (`update_type="incremental"`): Individual changes (e.g., "score changed by +2")
- **Snapshot** (`update_type="snapshot"`): Full state refresh (e.g., "here's the current scoreboard")

**Class Variables**:
- `event_type: str` - Type identifier
- `timestamp: datetime` - When change occurred
- `update_type: str` - "incremental" or "snapshot" (default: "incremental")
- `game_id: str | None` - Optional game identifier
- `metadata: JSONDict` - Additional context

**Methods**:
- `to_dict() -> dict` - Serialize to dictionary
- `from_dict(data: dict) -> DataEvent` - Deserialize from dictionary
- `get_change_magnitude() -> float | None` - Get quantifiable change (override in subclasses)

**Child Classes**:
- `PlayByPlayEvent` - Individual play events (incremental: +points per play)
- `ScoreboardSnapshotEvent` - Full scoreboard state (snapshot: complete current state)
- `OddsChangeEvent` - Odds changes (incremental: previous → current)
- `InjuryEvent` - Player injury updates (incremental: status changes)
- `GameStatusEvent` - Game state changes (incremental: status transitions)
- `NewsEvent` - News articles/updates
- `TeamStatsEvent` - Team statistics updates

## Key Differences

| Aspect | DataFact | DataEvent |
|--------|----------|-----------|
| **Access Pattern** | Pull/Query | Push/Stream |
| **Represents** | Current state | Change/Delta |
| **Optimization** | Fast queries, caching | Streaming, buffering |
| **State Tracking** | Just current state | Before/after state |
| **Use Case** | "What is X?" | "X changed" |
| **Derived From** | Events | Source of truth |

## Incremental vs Snapshot Updates

Events can be either **incremental** (delta) or **snapshot** (full state):

### Incremental Updates (`update_type="incremental"`)

**Purpose**: Represent individual changes/deltas

**Example**: `PlayByPlayEvent`
- Represents a single play: "+2 points scored"
- Score changed from 40-42 to 42-42
- Used for: Real-time streaming of individual plays

**Characteristics**:
- ✅ Represents a single change
- ✅ Includes change metadata (points scored, etc.)
- ✅ Optimized for streaming individual updates
- ✅ Used when tracking changes in real-time

### Snapshot Updates (`update_type="snapshot"`)

**Purpose**: Represent complete current state

**Example**: `ScoreboardSnapshotEvent`
- Represents full scoreboard: "Current score is 45-42"
- Complete state refresh from API pull
- Used for: Periodic refreshes, initial state, catch-up

**Characteristics**:
- ✅ Represents full current state
- ✅ Replaces incremental state
- ✅ Used when pulling latest state from API
- ✅ Useful for catch-up after disconnection

### When to Use Each

**Use Incremental**:
- Real-time play-by-play streaming
- Tracking individual changes as they occur
- Event-driven reactive behavior

**Use Snapshot**:
- Pulling latest scoreboard from API
- Initial state setup
- Periodic state refresh
- Catch-up after disconnection

**Aggregator Handling**:
- Aggregators handle both types automatically
- Incremental: Updates fact incrementally
- Snapshot: Replaces fact with full state

## Usage Examples

### Pulling a Fact (Current State)

```python
from agentx.data import GameScoreFact, OddsFact

# Agent queries current state
current_score = operator.pull_fact("game_score", game_id="game_123")
# Returns: GameScoreFact(home_score=45, away_score=42, ...)

current_odds = operator.pull_fact("current_odds", market_id="market_456")
# Returns: OddsFact(current_odds=1.85, ...)
```

### Subscribing to Events (Changes)

```python
from agentx.data import PlayByPlayEvent, OddsChangeEvent

# Agent subscribes to event stream
@agent.subscribe("play_by_play")
def handle_score_change(event: PlayByPlayEvent):
    print(f"Score changed: +{event.points} points")
    print(f"New score: {event.home_score}-{event.away_score}")

@agent.subscribe("odds_change")
def handle_odds_change(event: OddsChangeEvent):
    print(f"Odds changed: {event.previous_odds} -> {event.current_odds}")
    print(f"Change: {event.odds_change_percent}%")
```

### Deriving Facts from Events

```python
# Data Store maintains facts from events
def update_score_fact(event: PlayByPlayEvent) -> GameScoreFact:
    return GameScoreFact(
        game_id=event.game_id,
        home_team_id="LAL",  # From game metadata
        away_team_id="BOS",
        home_score=event.home_score,  # From latest event
        away_score=event.away_score,
        period=event.period,
        period_time=event.period_time,
        game_status="live",
        timestamp=event.timestamp,
    )
```

## Design Decisions

1. **Frozen Dataclasses**: Both Facts and Events are frozen (immutable) for thread safety and consistency
2. **Slots**: Using `slots=True` for memory efficiency
3. **Type Safety**: Strong typing with specific child classes for each fact/event type
4. **Serialization**: Built-in `to_dict()` and `from_dict()` methods for persistence
5. **Change Tracking**: Events include `get_change_magnitude()` for quantifiable changes

## See Also

- `_examples.py` - Usage examples and demonstrations
- `design/data-infrastructure.md` - Full design documentation

