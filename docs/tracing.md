# Tracing

DojoZero uses OpenTelemetry for distributed tracing, with support for Jaeger (local) and Alibaba Cloud SLS (production).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         WRITE PATH                               │
│                                                                  │
│  ┌──────────────┐              ┌──────────────┐    OTLP     ┌─────────┐
│  │ Trial Runner │ ──────────▶  │  Dashboard   │ ─────────▶  │ SLS/    │
│  │ --server     │              │  Server      │   export    │ Jaeger  │
│  └──────────────┘              └──────────────┘             └─────────┘
│                                                                    │
└────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                         READ PATH                                │
│                                                                  │
│  ┌──────────────┐    REST/WS    ┌──────────────┐   query     ┌─────────┐
│  │  Frontend    │ ◀──────────▶  │   Arena      │ ◀─────────  │ SLS/    │
│  │  (React)     │               │   Server     │             │ Jaeger  │
│  └──────────────┘               └──────────────┘             └─────────┘
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

## Backends

### Jaeger (Local Development)

Install Jaeger all-in-one ([download page](https://www.jaegertracing.io/download/)):

```bash
# Option 1: Homebrew (macOS)
brew install jaegertracing/tap/jaeger-all-in-one
jaeger-all-in-one

# Option 2: Docker
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4318:4318 \
  jaegertracing/all-in-one:latest
```

Run Dashboard Server with Jaeger:
```bash
# With default endpoints (localhost:4318)
dojo0 serve --trace-backend jaeger

# With custom endpoint
dojo0 serve --trace-backend jaeger --trace-ingest-endpoint http://localhost:4318
```

Run Arena Server with Jaeger:
```bash
# With default endpoint (localhost:16686)
dojo0 arena --trace-backend jaeger

# With custom endpoint
dojo0 arena --trace-backend jaeger --trace-query-endpoint http://localhost:16686
```

- UI: http://localhost:16686
- OTLP HTTP: http://localhost:4318
- Docs: https://www.jaegertracing.io/docs/

### SLS (Production)

**Prerequisites:**
1. Create an SLS Project in Alibaba Cloud console
2. Create a Trace Instance (this creates the logstore automatically)

**Configuration via environment variables:**

```bash
# Credentials (one of these methods):
# 1. Environment variables
export ALIBABA_CLOUD_ACCESS_KEY_ID=xxx
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=xxx

# 2. Credentials file (~/.alibabacloud/credentials)
# 3. ECS RAM role (automatic on ECS instances)
# 4. OIDC (K8s RRSA)

# SLS configuration
export DOJOZERO_SLS_PROJECT=my-project
export DOJOZERO_SLS_ENDPOINT=cn-hangzhou.log.aliyuncs.com
export DOJOZERO_SLS_LOGSTORE=dojozero-traces
```

Run Dashboard Server with SLS:
```bash
dojo0 serve --trace-backend sls --oss-backup
```

Run Arena Server with SLS:
```bash
dojo0 arena --trace-backend sls
```

## Running Trials with Tracing

**Option 1: Via Dashboard Server (recommended for production)**
```bash
# Terminal 1: Start Dashboard Server with SLS
dojo0 serve --trace-backend sls --oss-backup

# Terminal 2: Run trial
dojo0 run --params config.yaml --server http://localhost:8000
```

**Option 2: Local mode with Jaeger**
```bash
# Terminal 1: Start Jaeger
jaeger-all-in-one

# Terminal 2: Start Dashboard Server
dojo0 serve --trace-backend jaeger

# Terminal 3: Run trial
dojo0 run --params config.yaml --server http://localhost:8000
```

**Option 3: Local mode (no trace export)**
```bash
dojo0 run --params config.yaml
```

## Arena (Frontend)

Start Arena Server to view traces:
```bash
# With Jaeger (local dev)
dojo0 arena --trace-backend jaeger

# With SLS (production)
dojo0 arena --trace-backend sls
```

Then run the frontend:
```bash
cd frontend && npm run dev
# Open http://localhost:5173
```

## CLI Reference

### `dojo0 serve`

| Flag | Description |
|------|-------------|
| `--trace-backend` | `jaeger` or `sls` (required for tracing) |
| `--trace-ingest-endpoint` | OTLP endpoint for Jaeger (default: http://localhost:4318) |
| `--oss-backup` | Enable OSS backup for trial data |

### `dojo0 arena`

| Flag | Description |
|------|-------------|
| `--trace-backend` | `jaeger` or `sls` (required) |
| `--trace-query-endpoint` | Jaeger Query API (default: http://localhost:16686) |
| `--static-dir` | Path to frontend build output |

### Environment Variables (SLS)

| Variable | Description |
|----------|-------------|
| `DOJOZERO_SLS_PROJECT` | SLS project name |
| `DOJOZERO_SLS_ENDPOINT` | SLS endpoint (e.g., cn-hangzhou.log.aliyuncs.com) |
| `DOJOZERO_SLS_LOGSTORE` | Logstore name (e.g., dojozero-traces) |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` | Access key ID |
| `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | Access key secret |

## Components

| Component | Description |
|-----------|-------------|
| `OTelSpanExporter` | Exports spans via OTLP HTTP |
| `JaegerTraceReader` | Reads spans from Jaeger API |
| `SLSTraceReader` | Reads spans from SLS API |

## Span Types

| Operation | Description |
|-----------|-------------|
| `trial.started` / `trial.stopped` | Trial lifecycle |
| `agent.registered` / `datastream.registered` | Actor metadata |
| `agent.input` / `agent.response` / `agent.tool_result` | Agent conversation |
| `game_update`, `odds_update`, `play_by_play`, etc. | DataStream events |

## Standard Tags

All spans include:
- `dojozero.trial.id` - Trial identifier
- `dojozero.actor.id` - Actor identifier
- `dojozero.event.type` - Event type

See `design/2026-01-09-trace-data-design.md` for full schema.

## Arena UI

Arena reads traces via `/api/trials/{trial_id}` and derives:
- Actor list (from `*.registered` spans)
- Conversation history (from `agent.*` spans)
- Event timeline (from all spans)

## Programmatic Usage

```python
from dojozero.core import (
    OTelSpanExporter,
    emit_span,
    create_span_from_event,
    get_sls_exporter_headers,
)

# Initialize exporter for Jaeger
exporter = OTelSpanExporter(
    otlp_endpoint="http://localhost:4318",
    service_name="dojozero",
)

# Initialize exporter for SLS
exporter = OTelSpanExporter(
    otlp_endpoint="https://project.cn-hangzhou.log.aliyuncs.com",
    service_name="dojozero",
    headers=get_sls_exporter_headers(),
)

# Export span
span = create_span_from_event(
    trial_id="trial-123",
    actor_id="agent-1",
    operation_name="agent.response",
    extra_tags={"event.content": "Hello"},
)
exporter.export_span(span)
```
