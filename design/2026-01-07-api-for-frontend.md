# DojoZero Frontend API Design

The DojoZero Frontend API provides real-time streaming and historical playback capabilities for observing trial execution and agent activity.

## Design Goals

- Real-time data push: Stream spans via WebSocket
- Historical playback: Query complete span history from checkpoints
- Read-only frontend: Frontend is strictly for observation
- Unified storage: Traces derived from checkpoint data

Non-goals:

- Frontend control plane: No support for starting/stopping trials from frontend
- Separate trace storage: Traces are derived from checkpoints on-demand

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Dashboard Server                                 │
│                        (dojo0 serve, port 8000)                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌───────────────┐      ┌──────────────────┐      ┌────────────────┐   │
│   │   Dashboard   │─────▶│  DashboardStore  │◀─────│  REST API      │   │
│   │   (runtime)   │      │ (FS or InMemory) │      │  /api/trials   │   │
│   └───────────────┘      └──────────────────┘      │  /api/traces   │   │
│                                 │                   └────────────────┘   │
│                                 │                                        │
│                    ┌────────────┴────────────┐                          │
│                    │                         │                          │
│              ┌─────▼─────┐           ┌───────▼───────┐                  │
│              │  Trials   │           │  Checkpoints  │                  │
│              │  spec.json│           │  actor_states │                  │
│              │status.json│           │  → spans      │                  │
│              └───────────┘           └───────────────┘                  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
         │                                      │
         ▼                                      ▼
  GET /api/trials                      GET /api/traces
  GET /api/trials/{id}/status          GET /api/traces/{trial_id}
  POST /api/trials/{id}/stop



┌─────────────────────────────────────────────────────────────────────────┐
│                      Frontend Server (optional)                          │
│                      (dojo0 frontend, port 3001)                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌───────────────────┐      ┌──────────────────┐                       │
│   │  TraceReader      │─────▶│  SpanBroadcaster │                       │
│   │  (Dashboard/Jaeger)│     │  (WebSocket)     │                       │
│   └───────────────────┘      └──────────────────┘                       │
│                                      │                                   │
│                                      ▼                                   │
│                           ┌──────────────────┐                          │
│                           │ WebSocket Clients│                          │
│                           │  (per trial_id)  │                          │
│                           └──────────────────┘                          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
         │                                      │
         ▼                                      ▼
  GET /api/traces                      WS /ws/trials/{id}/stream
  GET /api/traces/{trial_id}              (snapshot + spans)
```

## Core Components

- **DashboardStore**: Storage for trials and checkpoints
- **Dashboard Server**: Trial lifecycle management and REST API
- **Frontend Server**: Optional layer for WebSocket streaming
- **TraceReader**: Protocol for reading traces from Dashboard or Jaeger

## API Design

### Dashboard Server Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/trials` | List all trials with status |
| `GET /api/trials/{id}/status` | Get detailed trial status |
| `POST /api/trials/{id}/stop` | Stop a running trial |
| `GET /api/traces` | List trial IDs with checkpoint data |
| `GET /api/traces/{trial_id}` | Get spans and agent_states for a trial |

#### Response: GET /api/traces/{trial_id}

```json
{
  "trial_id": "nba-trial",
  "spans": [
    {
      "traceID": "nba-trial",
      "spanID": "abc123",
      "operationName": "game_update",
      "startTime": 1736251800000000,
      "duration": 0,
      "tags": [...],
      "logs": []
    }
  ],
  "agent_states": {
    "betting_agent": {
      "state": [{"stream_id": [...messages...]}]
    }
  }
}
```

### Frontend Server Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/traces` | List trials from trace store |
| `GET /api/traces/{trial_id}` | Get spans for replay |
| `WS /ws/trials/{trial_id}/stream` | Real-time span streaming |

### WebSocket Protocol

Message types:

| Type | Description |
|------|-------------|
| `snapshot` | Complete span history on connection |
| `span` | New span as it occurs |
| `trial_ended` | Trial completion notification |
| `heartbeat` | Keep-alive signal |

Message format:

```json
{
  "type": "snapshot",
  "trial_id": "nba-trial",
  "timestamp": "2026-01-07T10:30:00Z",
  "data": {
    "spans": [...]
  }
}
```

## Trace Generation

Spans are generated on-demand from checkpoint data:

1. **DataStream events**: `{ event_type, timestamp, ... }` → SpanData
2. **Agent messages**: `{ role, content, timestamp, ... }` → SpanData

See [2026-01-09-span-design.md](2026-01-09-trace-data-design.md) for span format details.

## Deployment Modes

### Single Server (Development)

```
Frontend (React) ──────▶ Dashboard Server (8000)
                         GET /api/traces/{trial_id}
```

### Dual Server (Production)

```
Frontend (React) ──────▶ Frontend Server (3001) ──────▶ Dashboard Server (8000)
                         WS /ws/trials/{id}/stream     GET /api/traces/{trial_id}
```

### With External Trace Store

```
Dashboard Server ──────▶ OTLP Exporter ──────▶ Jaeger
                                                  │
Frontend (React) ──────▶ Frontend Server ─────────┘
                         (TraceReader: Jaeger)
```
