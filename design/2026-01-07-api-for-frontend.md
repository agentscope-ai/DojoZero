# DojoZero Frontend API Design

The DojoZero Frontend API provides real-time streaming and historical playback capabilities for observing trial execution and agent activity.

## Design Goals

Build a frontend-facing API layer for visualizing DojoZero's runtime state with the following requirements:

- Real-time data push: Stream trial status and events via WebSocket
- Historical playback: Query complete event history of completed trials
- Read-only frontend: Frontend is strictly for observation and visualization, with no control over backend execution
- Future-proof data structures: Prepare for eventual integration with OpenTelemetry Trace Store

Non-goals:

- Frontend control plane: No support for starting/stopping trials or other control operations
- Full Trace Store: Start with in-memory broadcast, migrate to persistent storage later

## Architecture

### Short-term Approach (In-Memory Broadcast)

```
┌──────────────────────────────────────────────────────────────┐
│                    dojo0 serve (FastAPI)                     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌───────────────┐      ┌──────────────────┐                │
│  │   Dashboard   │─────▶│  EventBroadcaster│                │
│  │   (runtime)   │      │   (in-memory)    │                │
│  └───────────────┘      └────────┬─────────┘                │
│         │                        │                          │
│         ▼                        ▼                          │
│  ┌───────────────┐      ┌──────────────────┐                │
│  │  FileSystem   │      │ WebSocket Clients│                │
│  │    Store      │      │  (per trial_id)  │                │
│  └───────────────┘      └──────────────────┘                │
│                                                              │
└──────────────────────────────────────────────────────────────┘
          │                        │
          ▼                        ▼
   GET /api/trials           WS /ws/trials/{id}/stream
   GET /api/trials/{id}/replay    (snapshot + events)
```

Core components:

- **EventBroadcaster**: Receives events from Dashboard actors, maintains WebSocket clients grouped by trial_id, and pushes events in real-time
- **Snapshot generation**: On client connection, retrieves current state from Dashboard and sends it as a package
- **Unified service**: Started via `dojo0 serve`, reuses Dashboard configuration and storage

### Long-term Approach (Trace Store)

```
┌────────────────────┐     ┌──────────────────┐     ┌─────────────┐
│  dojo0 serve       │────▶│  OTLP Exporter   │────▶│   Jaeger    │
│  (Dashboard)       │     │                  │     │ (or other)  │
└────────────────────┘     └──────────────────┘     └─────────────┘
                                                           │
                                                           │
                                                           ▼
┌────────────────────┐     ┌──────────────────┐     ┌─────────────┐
│     Frontend       │────▶│  Server API      │────▶│  Trace API  │
│                    │     │  /api/traces/*   │     │  Query      │
└────────────────────┘     └──────────────────┘     └─────────────┘
```

Migration to Trace Store will require only:
1. Replace EventBroadcaster with OTLP Exporter
2. Update REST API to query Trace Store
3. Frontend data structures remain unchanged

## Data Model

### LiveEvent (OpenTelemetry-Compatible)

Unified event format that maps directly to OpenTelemetry Span:

```python
@dataclass
class LiveEvent:
    # Trace identification
    trace_id: str                    # trial_id, links all events in the same trial
    span_id: str                     # Unique event ID
    parent_span_id: str | None       # Parent event ID for causal tracking
    
    # Timing information
    timestamp: datetime              # Event occurrence time
    duration_ms: float | None        # Event duration (optional)
    
    # Classification
    kind: str                        # "event" | "span" | "metric"
    name: str                        # Human-readable event name
    source: str                      # Actor ID that produced this event
    source_type: str                 # "agent" | "broker" | "stream" | "system"
    
    # Payload
    attributes: dict[str, Any]       # Structured key-value pairs
    
    # Status
    status: str                      # "ok" | "error" | "timeout"
    error_message: str | None        # Error message (optional)
```

Design principles:
- **Trial as Trace**: One trial corresponds to one trace_id
- **Actor operations as Spans**: Agent decisions, broker settlements, etc. are independent spans
- **Data events as Span Events**: game_update, odds_update, etc. are events within spans
- **Preserve hierarchy**: Track causal chains via parent_span_id

### WebSocket Message Types

Only 4 message types:

```python
class WSMessageType(Enum):
    SNAPSHOT = "snapshot"            # Complete state sent on connection
    EVENT = "event"                  # Single real-time event
    TRIAL_ENDED = "trial_ended"      # Trial completion notification
    HEARTBEAT = "heartbeat"          # Heartbeat (optional)
```

## API Design

### REST Endpoints

```
GET  /api/trials                    # List all trials
GET  /api/trials/{trial_id}/replay  # Get event history of completed trial
```

Detailed trial state and agent information are obtained via WebSocket snapshot.

### WebSocket Endpoint

```
WS  /ws/trials/{trial_id}/stream    # Subscribe to trial real-time stream
```

Protocol:
- Client needs not send any message after connecting
- Server immediately pushes `snapshot`, then continuously pushes `event` messages
- Unidirectional push; client only listens

### Message Format Examples

**Snapshot on connection**:

```json
{
  "type": "snapshot",
  "trial_id": "xxx",
  "timestamp": "2026-01-07T10:30:00Z",
  "data": {
    "metadata": { "home_team": "Lakers", "away_team": "Warriors", "game_id": "..." },
    "score": { "home": 85, "away": 82, "period": 3, "clock": "5:32" },
    "odds": { "home": 1.45, "away": 2.80 },
    "agents": [
      { "actor_id": "shark", "balance": 850, "total_bets": 3 },
      { "actor_id": "whale", "balance": 1200, "total_bets": 1 }
    ],
    "recent_events": [ /* List of recent events */ ]
  }
}
```

**Subsequent event push**:

```json
{
  "type": "event",
  "trial_id": "xxx", 
  "timestamp": "2026-01-07T10:30:15Z",
  "data": { /* LiveEvent structure */ }
}
```

**Trial end notification**:

```json
{
  "type": "trial_ended",
  "trial_id": "xxx",
  "timestamp": "2026-01-07T12:00:00Z"
}
```

## Implementation Plan

### Phase 1: MVP (In-Memory Broadcast)

1. **Implement `dojo0 serve` command**
   - Based on FastAPI + uvicorn
   - Reuse Dashboard's store and runtime configuration
   - Implement REST endpoints

2. **EventBroadcaster**
   - Receive events from Dashboard actors
   - Manage WebSocket clients grouped by trial_id
   - Push events in real-time

3. **WebSocket streaming**
   - Implement `/ws/trials/{trial_id}/stream`
   - Generate and send snapshot on connection
   - Continuously push real-time events

### Phase 2: Trace Store Integration

1. **OTLP Exporter**
   - EventBroadcaster writes to OTLP concurrently

2. **Trace Query**
   - Change `/api/trials/{id}/replay` to query Trace Store
   - Or allow frontend to query Jaeger API directly
