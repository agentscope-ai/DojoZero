# Tracing

DojoZero uses OpenTelemetry for distributed tracing with Jaeger as the trace backend.

## Backend: Jaeger

Install Jaeger: [https://www.jaegertracing.io/](https://www.jaegertracing.io/)

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

## Running Trials with Tracing

**Option 1: Via Dashboard Server (recommended)**
```bash
# Terminal 1: Start Dashboard Server
dojo0 serve --trace-backend jaeger

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
dojo0 arena --trace-backend jaeger
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
| `--trace-backend` | `jaeger` (required for tracing) |
| `--trace-ingest-endpoint` | OTLP endpoint for Jaeger (default: http://localhost:4318) |

### `dojo0 arena`

| Flag | Description |
|------|-------------|
| `--trace-backend` | `jaeger` (required) |
| `--trace-query-endpoint` | Jaeger Query API (default: http://localhost:16686) |
| `--static-dir` | Path to frontend build output |

## Components

| Component | Description |
|-----------|-------------|
| `OTelSpanExporter` | Exports spans via OTLP HTTP |
| `JaegerTraceReader` | Reads spans from Jaeger API |

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
