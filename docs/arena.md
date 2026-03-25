# Arena

Use Arena to inspect trace timelines and trial activity in a browser UI.

## 1. Start Arena

Start the Arena server:

```bash
# With default endpoint (localhost:16686)
dojo0 arena --trace-backend jaeger

# With custom endpoint
dojo0 arena --trace-backend jaeger --trace-query-endpoint http://localhost:16686
```

## 2. Run the Frontend (Development)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## 3. CLI Reference

### `dojo0 arena`

| Option | Description |
|---|---|
| `--trace-backend` | `jaeger` or `sls` (SLS requires `dojozero[alicloud]`) |
| `--trace-query-endpoint` | Jaeger Query API endpoint (default: `http://localhost:16686`) |
| `--static-dir` | Path to frontend build output |

### `dojo0 serve` (related tracing option)

| Option | Description |
|---|---|
| `--trace-backend` | `jaeger` (default stack) or `sls` (requires `dojozero[alicloud]`) |
| `--trace-ingest-endpoint` | OTLP ingest endpoint for Jaeger (default: `http://localhost:4318`) |

## 4. Programmatic Usage

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
