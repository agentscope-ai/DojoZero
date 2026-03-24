# Tracing

DojoZero uses OpenTelemetry for distributed tracing.

**Install note:** **Jaeger** works with the **default** package install. **Alibaba Cloud Log Service (SLS)** as a trace backend requires optional dependencies: `pip install 'dojozero[alicloud]'` (see [`installation.md`](./installation.md)).

## Backend: Jaeger

Install and start Jaeger: [https://www.jaegertracing.io/](https://www.jaegertracing.io/)

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

## Backend: Alibaba Cloud SLS (optional)

Use `--trace-backend sls` only after installing **`dojozero[alicloud]`**. Configure SLS project/endpoint/logstore via `DOJOZERO_SLS_*` (see `.env.example`). For querying traces in Arena/dashboard, the same extra is required.

## Running Trials with Tracing

**Option 1: Via Dashboard Server (recommended)**
```bash
# Terminal 1: Start Dashboard Server
dojo0 serve --trace-backend jaeger

# Terminal 2: Run trial
dojo0 run --params config.yaml --server http://localhost:8000
```

**Option 2: Standalone Useage**
```bash
dojo0 run --params config.yaml --trace-backend jaeger
```

## Arena (Frontend)

Start Arena Server to view traces:
```bash
dojo0 arena --trace-backend jaeger
```

Then run the frontend:
```bash
cd frontend && npm install && npm run dev
# Open http://localhost:5173
```

## CLI Reference

### `dojo0 serve`

| Option | Description |
|------|-------------|
| `--trace-backend` | `jaeger` (default stack) or `sls` (needs `dojozero[alicloud]`) |
| `--trace-ingest-endpoint` | OTLP endpoint for Jaeger (default: http://localhost:4318) |

### `dojo0 arena`

| Option | Description |
|------|-------------|
| `--trace-backend` | `jaeger` or `sls` (SLS needs `dojozero[alicloud]`) |
| `--trace-query-endpoint` | Jaeger Query API (default: http://localhost:16686) |
| `--static-dir` | Path to frontend build output |

## Programmatic Usage

```python
from dojozero.core._tracing import (
    OTelSpanExporter,
    create_trace_reader,
    emit_span,
    set_otel_exporter,
)

# Initialize exporter for Jaeger
exporter = OTelSpanExporter(
    "http://localhost:4318",
    service_name="my-service",
    headers=None,
)
exporter.start()
set_otel_exporter(exporter)

# Create trace reader for querying
reader = create_trace_reader(
    backend="jaeger",
    trace_query_endpoint="http://localhost:16686",
    service_name="my-service",
)
```
