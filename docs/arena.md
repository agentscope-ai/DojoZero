# Arena

Use Arena to inspect trace timelines and trial activity in a browser UI.

## 1. Running Arena

Build the React app once; Arena serves `frontend/dist` on the same port as the API.

```bash
cd frontend
npm install
npm run build
```

From the **repository root**:

```bash
# With default Jaeger Query (localhost:16686)
dojo0 arena --trace-backend jaeger --static-dir frontend/dist

# Custom Jaeger Query URL (replace host/port with your deployment)
dojo0 arena --trace-backend jaeger --trace-query-endpoint http://jaeger-query:16686 --static-dir frontend/dist
```

Open **http://localhost:3001** (or the host/port from `--host` / `--port`).

### Development mode

**Vite** serves the UI; Arena handles `/api` and `/ws` only (omit `--static-dir`).

Terminal 1 — from the **repository root**:

```bash
dojo0 arena --trace-backend jaeger --trace-query-endpoint http://localhost:16686
```

Terminal 2:

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. Vite proxies `/api` and `/ws` to `http://localhost:3001` (override with `VITE_API_URL` if needed).

## 2. CLI Reference

### `dojo0 arena`

| Option | Description |
|---|---|
| `--config` | Optional YAML for cache / WebSocket / SLS query limits (server bind and trace settings still come from CLI). |
| `--host` | Bind address (default: `127.0.0.1`). |
| `--port` | Listen port (default: `3001`). |
| `--trace-backend` | `jaeger` or `sls` (`sls` requires `dojozero[alicloud]` and env vars). |
| `--trace-query-endpoint` | Jaeger Query API base URL (default: `http://localhost:16686`). Only used with `--trace-backend=jaeger`. |
| `--service-name` | Service name for trace queries (default: `dojozero`). |
| `--static-dir` | Path to frontend build output (`frontend/dist`). Optional; omit in Vite dev mode. |

### `dojo0 serve` (related tracing option)

| Option | Description |
|---|---|
| `--trace-backend` | Optional. Pass `jaeger` or `sls` to export traces; if omitted, tracing is disabled. `sls` requires `dojozero[alicloud]` and env vars. |
| `--trace-ingest-endpoint` | OTLP HTTP ingest URL for Jaeger (default: `http://localhost:4318`). Only used with `--trace-backend=jaeger`. |
| `--service-name` | Trace `service.name` (default: `dojozero`). |

## 3. Programmatic Usage

```python
from dojozero.core._tracing import (
    OTelSpanExporter,
    create_trace_reader,
    set_otel_exporter,
)

# Initialize exporter for Jaeger (OTLP HTTP, e.g. Jaeger collector on 4318)
exporter = OTelSpanExporter(
    otlp_endpoint="http://localhost:4318",
    service_name="my-service",
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
