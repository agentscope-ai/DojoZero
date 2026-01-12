# Dashboard and Frontend API Redesign

## Overview

We want to decouple dashboard and frontend server APIs.

Dashboard is going to be responsible for managing actors
and their lifecycles. It uses Open Telemetry traces to
export regular actions performed by the actors. The traces
are exported to a trace store. For the time being, let's
use Jaeger as our trace store.

Frontend server is going to be responsible for querying
the Jaeger trace store and returning historical events
to the frontend UI. It is also responsible for forwarding
realtime actions performed by the actors observed via the
trace store. For now, we can use simple polling mechanism
instead of advance streaming -- think about it as a future
work.

The frontend UI relies solely on the frontend server for data
needs. The frontend server and the UI are completely decoupled
from the dashboard at runtime. The only communication is via
the trace store.

## Dashboard via `dojo0 serve`

The work is alreayd on-going. We want to enable serving endpoint
for the dashboard. The dashboard server, after started using
`dojo0 serve` command, can accept runs submitted through 
`dojo0 run` command. If the work is already started please continue
otherwise please implement it.

## Dashboard trace export

Dashboard will use Open Telemetry traces. For now, let's reference
`design/2026-01-09-trace-data-design.md` for span definitions -- you
may need to make changes (but don't overwrite that file). For now,
let's add spans directly using the `opentelemetry-sdk` package.
User can configure the OTEL endpoint for exporting. For realtime tracing
we don't want to batch export.

## Frontend Server API

The Frontend Server is the sole data source for the browser UI. It polls
the trace store (Jaeger) for new spans and streams them to connected clients.
The browser never communicates directly with Dashboard or Jaeger.

### Endpoints

```
GET  /api/traces                    - List all trial IDs
GET  /api/traces/{trial_id}         - Get complete trace for replay
WS   /ws/trials/{trial_id}/stream   - Real-time span streaming
GET  /health                        - Health check
```

### WebSocket Protocol

The WebSocket endpoint polls Jaeger (1 second interval) and streams new spans:

```typescript
// Server -> Client messages
type WSMessage =
  | { type: "snapshot", data: { spans: SpanData[] } }  // Initial state
  | { type: "span", data: SpanData }                   // New span
  | { type: "trial_ended" }                            // Trial completed
  | { type: "heartbeat" }                              // Keep-alive
```

The frontend processes spans into derived state:
- **actors**: Extract from `*.registered` spans (actor metadata)
- **conversations**: Group `agent.*` spans by actor_id and stream_id
- **events**: Filter non-registration, non-agent spans for timeline

### Data Transformation

Raw span data from Jaeger is verbose. Key transformations:

1. **Tag Flattening**: Convert `tags: [{key, value}]` array to map
2. **Timestamp Normalization**: Ensure consistent microsecond epoch format
3. **Field Filtering**: Drop process info, refs - keep only UI-needed fields

### Response Format

```json
{
  "trial_id": "nba-trial",
  "spans": [
    {
      "traceID": "nba-trial",
      "spanID": "abc123",
      "operationName": "agent.registered",
      "startTime": 1736251800000000,
      "duration": 0,
      "tags": [
        {"key": "dojozero.actor.id", "value": "betting_agent"},
        {"key": "resource.model", "value": "gpt-4-turbo"}
      ]
    }
  ]
}
```

## Architecture Summary

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Dashboard     │────▶│   Jaeger/OTLP   │◀────│ Frontend Server │
│  (dojo0 serve)  │     │  (trace store)  │     │ (dojo0 frontend)│
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
       OTLP export                         Polls (1s)    │ WebSocket
                                                         ▼
                                                ┌─────────────────┐
                                                │   Browser UI    │
                                                └─────────────────┘
```

Key design decisions:
- **Polling over streaming**: Simpler, works with any Jaeger deployment
- **Frontend Server as gateway**: Browser only talks to Frontend Server
- **Dashboard decoupled**: Only writes to OTLP, doesn't serve UI data