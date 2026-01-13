# Dashboard and Frontend API Design

## Overview

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Dashboard     │────▶│   Jaeger/OTLP   │◀────│ Frontend Server │
│  (dojo0 serve)  │     │  (trace store)  │     │ (dojo0 frontend)│
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
               OTLP export                 Polls (1s)    │ WebSocket
                                                         ▼
                                                ┌─────────────────┐
                                                │   Browser UI    │
                                                └─────────────────┘
```

The dashboard and frontend server APIs are decoupled by design.

Dashboard is responsible for managing actors
and their lifecycles. It uses Open Telemetry traces to
export actions performed by the actors. The traces are
exported to a trace store (Jaeger).

Frontend server is responsible for querying the Jaeger trace
store and returning historical events to the frontend UI. It
also forwards realtime actions performed by the actors observed
via the trace store. The frontend server uses a polling mechanism
to query the trace store (streaming may be considered as future work).

The frontend UI relies solely on the frontend server for data
needs. The frontend server and the UI are completely decoupled
from the dashboard at runtime. The only communication is via
the trace store.

## Dashboard via `dojo0 serve`

The Dashboard exposes a serving endpoint via the `dojo0 serve` command.
Once started, the dashboard server accepts trial submissions through 
the `dojo0 run` command.

## Frontend Server API

The Frontend Server is the sole data source for the browser UI. It polls
the trace store (Jaeger) for new spans and streams them to connected clients.
The browser never communicates directly with Dashboard or Jaeger.

## Quick Start

### 1. Start Jaeger (Trace Store)

```bash
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  -p 4318:4318 \
  jaegertracing/all-in-one:latest
```

### 2. Start Dashboard Server

```bash
dojo0 serve --host 0.0.0.0 --port 8000 --otlp-endpoint http://localhost:4318
```

### 3. Submit a Trial

```bash
dojo0 run --params configs/nba-pregame-betting.yaml --trial-id test --server http://localhost:8000
```

### 4. Start Frontend Server

```bash
dojo0 frontend --host 0.0.0.0 --port 3001 --trace-store http://localhost:16686
```

### 5. Start React UI (Development)

```bash
cd frontend
npm install  # first time only
npm run dev
```

Open http://localhost:5173 in browser.

## Dashboard Server API

The Dashboard Server (`dojo0 serve`) manages trials and emits OTLP traces.

### Endpoints

```
# Trial Management
GET  /api/trials                    - List all trials with status
POST /api/trials                    - Submit new trial
GET  /api/trials/{trial_id}/status  - Get detailed trial status
POST /api/trials/{trial_id}/stop    - Stop running trial

# System
GET  /health                        - Health check
```

### CLI Options

```bash
dojo0 serve [OPTIONS]

Options:
  --host TEXT          Host address (default: 127.0.0.1)
  --port INT           Port number (default: 8000)
  --otlp-endpoint URL  OTLP endpoint for trace export (e.g., http://localhost:4318)
```

### Trial Submit Request

```json
{
  "trial_id": "optional-trial-id",
  "scenario": {
    "name": "nba_moneyline.pregame_betting",
    "module": "dojozero.nba_moneyline",
    "config": {...}
  },
  "metadata": {...},
  "resume": {"checkpoint_id": "...", "latest": false},
  "replay": {"file": "/path/to/events.jsonl", "speed_up": 2.0, "max_sleep": 20.0}
}
```

## Frontend Server API

The Frontend Server (`dojo0 frontend`) reads from trace store and streams to browsers.

### Endpoints

```
# Trace Queries (from Jaeger)
GET  /api/traces                    - List all trial IDs
GET  /api/traces/{trial_id}         - Get complete trace for replay

# Trial Info (derived from traces)
GET  /api/trials                    - List trials with phase/metadata
GET  /api/trials/{trial_id}         - Get trial info

# WebSocket
WS   /ws/trials/{trial_id}/stream   - Real-time span streaming

# System
GET  /health                        - Health check
```

``