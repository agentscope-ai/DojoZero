# CLI Usage

This guide combines local CLI usage, server-based orchestration, trial scheduling, Arena, and frontend workflows.

## 1) Install and Minimum Environment Setup

```bash
# Install from source
uv pip install .

# Create local environment file from template
cp .env.example .env
```

Minimum requirement: add these two keys in `.env`.

```bash
DOJOZERO_DASHSCOPE_API_KEY=your_key
DOJOZERO_TAVILY_API_KEY=your_key
```

For full setup details (environment variables, trial config, and agent config), see [`docs/configuration.md`](./configuration.md).

## 2) Installation Options

If you plan to run DojoZero outside Docker or do local development, choose one install track:

| Track | Command | Use when |
|--------|---------|----------|
| **Default (Recommended)** | `uv pip install .` | Trials, dashboard, Jaeger tracing |
| **+ Alibaba Cloud/ Redis** | `uv pip install '.[alicloud,redis]'` | OSS backup / `oss://` paths, **`--trace-backend sls`**, sync-service Redis |

Details, package lists, and development setup: [`docs/installation.md`](./installation.md).

## 3) Run a Single Trial Locally

Use a trial params file from `trial_params/`:

```bash
dojo0 run --params trial_params/nba-moneyline.yaml --trial-id nba-local-001
```

If `--trial-id` is omitted, DojoZero generates a UUID.

## 4) Resume an Interrupted Trial

```bash
# Resume from a specific checkpoint
dojo0 run --trial-id nba-local-001 --checkpoint-id <checkpoint_id>

# Resume latest checkpoint
dojo0 run --trial-id nba-local-001 --resume-latest
```

You can also start from params + checkpoint together.

## 5) Run Through Dashboard Server

Start tracing backend and dashboard:

```bash
dojo0 serve --trace-backend jaeger
```

Submit a trial from another terminal:

```bash
dojo0 run \
  --params trial_params/nba-moneyline.yaml \
  --trial-id nba-server-001 \
  --server http://localhost:8000
```

## 6) Automatic Scheduling with Trial Sources

Trial sources let the server discover upcoming games and schedule trials before start time.

Run server with one or more sources:

```bash
dojo0 serve --trace-backend jaeger --trial-source "trial_sources/*.yaml"
```

Manage sources and schedules:

```bash
dojo0 list-sources
dojo0 list-trials
dojo0 remove-source <source_id>
dojo0 clear-schedules
```

## 7) Runtime and Store Options

These CLI options apply to `run`, `backtest`, and/or `serve`:

- `--store-directory`: trial state and checkpoint storage path
- `--runtime-provider {local,ray}`: select execution backend
- `--ray-config`: Ray initialization YAML

Example:

```bash
dojo0 run \
  --params trial_params/nfl-moneyline.yaml \
  --store-directory ./dojozero-store \
  --runtime-provider local
```

## 8) Auto-Resume Behavior in Server Mode

By default, `dojo0 serve` attempts to recover trials that were active before shutdown:

1. Finds trials in STARTING/RUNNING states
2. Locates latest checkpoint
3. Restarts from checkpoint (when not stale)

Useful options:

- `--no-auto-resume`: disable this behavior
- `--stale-threshold-hours`: skip very old checkpoints

## 9) Arena Server (Trace Viewer Backend)

Start Arena server (reads traces for live/replay visualization):

```bash
# Use default Jaeger query endpoint (http://localhost:16686)
dojo0 arena --trace-backend jaeger

# Or set a custom query endpoint
dojo0 arena --trace-backend jaeger --trace-query-endpoint http://localhost:16686
```

Arena-specific options:

- `--trace-backend {jaeger,sls}`
- `--trace-query-endpoint`: query API endpoint for trace backend
- `--static-dir`: path to built frontend assets

## 10) Frontend (Arena UI)

For local UI development:

```bash
cd frontend-update
npm install
npm run dev
```

Then open `http://localhost:5173`.

For production-style serving through `dojo0 arena`, build frontend assets first:

```bash
cd frontend-update
npm install
npm run build

# then from repo root:
dojo0 arena --trace-backend jaeger --static-dir frontend-update/dist
```

## 12) Server Mode and Tracing Notes

For Jaeger, open `http://localhost:16686` to explore traces.
If Jaeger is not installed yet, follow [`docs/tracing.md`](./tracing.md).

## 13) Command Index

Core commands covered in this guide:

- `dojo0 run`: start a local or server-submitted trial
- `dojo0 serve`: dashboard/orchestration server
- `dojo0 arena`: trace-backed Arena server
- `dojo0 backtest`: replay persisted event streams
- `dojo0 list-sources` / `dojo0 remove-source`: manage trial sources
- `dojo0 list-trials` / `dojo0 clear-schedules`: inspect and manage scheduled runs

For **NBA/NFL game batching** via `tools/nba_trial_runner.py` and `tools/nfl_trial_runner.py`, see [`docs/trial-runners.md`](./trial-runners.md).
