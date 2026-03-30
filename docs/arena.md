# Arena

Arena is a browser-based UI for exploring what happened during a trial. It visualizes the OpenTelemetry traces produced by DojoZero — showing you a timeline of events, agent messages, decisions, and trial lifecycle, all in one place.

> **Prerequisite:** You need a running trace backend (Jaeger or SLS) with trace data from at least one trial. See [Tracing](./tracing.md) for setup.

**Requirements:** Arena does **not** require the dashboard server (`dojo0 serve`). It **does** require a **trace query backend** reachable from the Arena process — Jaeger Query when using `--trace-backend jaeger`, or SLS when using `--trace-backend sls` — because the UI loads trial and span data from that API.


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