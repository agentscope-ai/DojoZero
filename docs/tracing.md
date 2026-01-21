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
dojo0 serve \
  --otlp-endpoint http://localhost:4318 \
  --trace-backend jaeger
```

- UI: http://localhost:16686
- OTLP HTTP: http://localhost:4318
- Docs: https://www.jaegertracing.io/docs/

### SLS (Production)

```bash
# Configure credentials (one of these methods):
# 1. Environment variables
export ALIBABA_CLOUD_ACCESS_KEY_ID=xxx
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=xxx

# 2. Credentials file (~/.alibabacloud/credentials)
# 3. ECS RAM role (automatic on ECS instances)
# 4. OIDC (K8s RRSA)

# Configure SLS
export DOJOZERO_SLS_PROJECT=my-project
export DOJOZERO_SLS_ENDPOINT=cn-hangzhou.log.aliyuncs.com
export DOJOZERO_SLS_LOGSTORE=dojozero-traces

# Run Dashboard Server with SLS
dojo0 serve \
  --otlp-endpoint https://my-project.cn-hangzhou.log.aliyuncs.com \
  --trace-backend sls
```

## Running Trials with Tracing

**Option 1: Via Dashboard Server (recommended for production)**
```bash
# Terminal 1: Start Dashboard Server
dojo0 serve \
  --otlp-endpoint https://my-project.cn-hangzhou.log.aliyuncs.com \
  --trace-backend sls \
  --oss-backup

# Terminal 2: Run trial
dojo0 run --params config.yaml --server http://localhost:8000
```

**Option 2: Local mode (no trace export)**
```bash
dojo0 run --params config.yaml
```

## Arena (Frontend)

Start Arena Server to view traces:
```bash
# With Jaeger
dojo0 arena --trace-store http://localhost:16686 --trace-backend jaeger

# With SLS
dojo0 arena \
  --trace-store https://my-project.cn-hangzhou.log.aliyuncs.com \
  --trace-backend sls
```

Then run the frontend:
```bash
cd frontend && npm run dev
# Open http://localhost:5173
```

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

# Initialize exporter
exporter = OTelSpanExporter(
    otlp_endpoint="http://localhost:4318",
    service_name="dojozero",
    headers=get_sls_exporter_headers(),  # For SLS
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
