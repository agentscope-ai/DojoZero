# DojoZero

DojoZero is a system for hosting AI agents that run continously on realtime data
to reason about future outcomes and act on them, such as trading and placing bets.

> 🚧 This project is in early development. Architecture design and core features are currently being implemented.
> See [Design](./design/) for decision records on design.

## Installation

1. Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
2. Install the package (runtime-only dependencies) from the repo root:

```bash
uv pip install . 
```

This installs DojoZero for running trials only. For development workflows (tests,
lint, editable installs), see the [Development Setup](#development-setup)
section below.

## Standalone Usage

DojoZero trials start from a params file that names the trial builder and its
inputs. Each builder defines the scenario—the combination of data streams,
operators, and agents that act together during the trial. Use `dojo0
list-builders` to see registered scenarios, then run `dojo0 get-builder
<name> --create-example-params` (or author the params file directly):

```yaml
# sample_trial.yaml
scenario:
	name: samples.bounded-random
	config:
		total_events: 5
		interval_seconds: 0.0
```

Launch the trial by pointing `dojo0 run` at the params file (add `--trial-id`
to set a friendly identifier, otherwise a UUID is generated):

```bash
dojo0 run --params sample_trial.yaml --trial-id sample-trial
```

Use `dojo0 list-builders` to discover registered scenarios (their
data streams, operators, and agents), and add imports with repeated `--import-module`
flags whenever your builder lives outside the defaults.

### Resuming trials

Resume without re-supplying the spec by combining `--trial-id` with either a
checkpoint id or the `--resume-latest` flag:

```bash
# Resume from a known checkpoint
dojo0 run --trial-id sample-trial --checkpoint-id 3f2c6a9e

# Resume from the latest checkpoint stored for the trial
dojo0 run --trial-id sample-trial --resume-latest
```

You can still start a new trial from a checkpoint by supplying both `--params`
and `--checkpoint-id`; the CLI applies the checkpoint before launching.

### Replay Mode (Backtesting)

Replay historical events from a JSONL file for backtesting:

```bash
dojo0 replay \
  --replay-file outputs/nba_betting_events.jsonl \
  --params configs/nba-pregame-betting.yaml \
  --replay-speed-up 2.0 \
  --replay-max-sleep 20.0
```

## Quick Start (Server Mode)

Run DojoZero with a web UI for real-time monitoring:

```bash
# 1. Start Jaeger (trace store)
docker run -d --name jaeger \
  -p 16686:16686 -p 4317:4317 -p 4318:4318 \
  jaegertracing/all-in-one:latest


# 2. Start Dashboard Server (manages trials, exports traces)
dojo0 serve --host 0.0.0.0 --port 8000 --trace-backend jaeger

# 3. Submit a trial (in another terminal)
dojo0 run --params configs/nba-pregame-betting.yaml --trial-id test --server http://localhost:8000

# Or submit a replay trial for backtesting
dojo0 replay --params configs/nba-pregame-betting.yaml --replay-file outputs/nba_betting_events.jsonl --trial-id replay-test --replay-speed-up 1.0 --replay-max-sleep 20 --server http://localhost:8000

# 4. Start Arena Server (serves WebSocket to browser)
dojo0 arena --host 0.0.0.0 --port 3001 --trace-backend jaeger

# 5. Start React UI (in another terminal)
cd frontend && npm install && npm run dev
```

Open http://localhost:5173 to view the arena UI.

## Server Usage

The `dojo0 serve` command starts a FastAPI dashboard server that provides REST APIs for managing trials and streaming real-time events:

- **Dashboard Server** (port 8000): Trial management, OTLP trace export
- **Arena Server** (port 3001): WebSocket streaming, trace queries

### Dashboard Server

```bash
# Start with Jaeger trace backend (development)
dojo0 serve --host 0.0.0.0 --port 8000 --trace-backend jaeger

# With SLS trace backend (production - Alibaba Cloud)
# Set env vars: DOJOZERO_SLS_PROJECT, DOJOZERO_SLS_ENDPOINT, DOJOZERO_SLS_LOGSTORE
dojo0 serve --host 0.0.0.0 --port 8000 --trace-backend sls

# With OSS backup for event data
dojo0 serve --host 0.0.0.0 --port 8000 --trace-backend sls --oss-backup

# With settings file
dojo0 --setting dojozero.yaml serve --host 0.0.0.0 --port 8000 --trace-backend jaeger
```


CLI options:
- `--host` - Host address to bind to (default: 127.0.0.1)
- `--port` - Port to listen on (default: 8000)
- `--trace-backend {jaeger,sls}` - Trace backend type
- `--trace-ingest-endpoint` - OTLP endpoint for Jaeger trace ingestion (default: http://localhost:4318)
- `--service-name` - Service name for trace export (default: dojozero)
- `--oss-backup` - Enable OSS backup for trial data (requires env vars)

API endpoints:
- `GET /api/trials` - List all trials with status
- `POST /api/trials` - Submit a new trial
- `GET /api/trials/{id}/status` - Get detailed trial status
- `POST /api/trials/{id}/stop` - Stop a running trial

### Arena Server

```bash

# Start with Jaeger as trace source (development)
dojo0 arena --host 0.0.0.0 --port 3001 --trace-backend jaeger

# Start with SLS as trace source (production)
# Set env vars: DOJOZERO_SLS_PROJECT, DOJOZERO_SLS_ENDPOINT, DOJOZERO_SLS_LOGSTORE
dojo0 arena --host 0.0.0.0 --port 3001 --trace-backend sls
```


CLI options:
- `--host` - Host address to bind to (default: 127.0.0.1)
- `--port` - Port to listen on (default: 3001)
- `--trace-backend {jaeger,sls}` - Trace backend type (required)
- `--trace-query-endpoint` - Jaeger Query API endpoint (default: http://localhost:16686)
- `--service-name` - Service name for trace queries (default: dojozero)
- `--static-dir` - Path to built static assets to serve (optional, for production)

**Production deployment** (single server serves both API and frontend):

```bash
# Build the React frontend first
cd frontend && npm run build

# Production with Jaeger
dojo0 arena --trace-backend jaeger --static-dir ./frontend/dist

# Production with SLS (recommended for cloud deployment)
# Credentials via env vars or ~/.alibabacloud/credentials or ECS RAM role
export DOJOZERO_SLS_PROJECT="your-project"
export DOJOZERO_SLS_ENDPOINT="cn-hangzhou.log.aliyuncs.com"
export DOJOZERO_SLS_LOGSTORE="dojozero-traces"
dojo0 arena --host 0.0.0.0 --port 3001 --trace-backend sls --static-dir ./frontend/dist
```

API endpoints:
- `GET /api/trials` - List trials with phase/metadata
- `GET /api/trials/{trial_id}` - Get trial info and spans
- `GET /api/landing` - Aggregated landing page data (cached)
- `GET /api/stats` - Real-time stats (games, wagered, etc.)
- `GET /api/games` - All games with filters (live, upcoming, completed)
- `GET /api/leaderboard` - Agent rankings by winnings
- `GET /api/agent-actions` - Recent agent actions for live ticker
- `WS /ws/trials/{trial_id}/stream` - Real-time span streaming

## Arena UI Development

```bash
cd frontend
npm install      # First time only
npm run dev      # Start dev server at http://localhost:5173
```

Ensure Arena Server is running at `http://localhost:3001`.

## Runtime & Store Configuration

When you need persistent dashboard storage, non-default imports, or an
alternative runtime provider, declare those dashboard settings once in
`dojozero.yaml` (or any filename you pass to the top-level `--setting` flag).
The settings file captures everything the dashboard runtime needs: store,
runtime provider, and module imports.

```yaml
# dojozero.yaml
store:
	kind: filesystem
	root: ./dojozero-store
runtime:
	kind: local
imports:
	- dojozero.samples
```

Launch with `dojo0 --setting dojozero.yaml run --params sample_trial.yaml` (or the
serve command once available) to reuse the configuration across invocations.

### Using the Ray runtime

Switch to Ray by setting `runtime.kind: ray` and passing any `ray.init`
arguments through `init_kwargs`. Install the extras first via
`uv pip install ".[ray]"`.

```yaml
runtime:
	kind: ray
	auto_init: false
	init_kwargs:
		address: auto
		num_cpus: 8
```

Then run `dojo0 --setting ray.yaml run --params sample_trial.yaml` to launch the
trial with Ray actors.

## Development Setup

For local development (tests, linting, pre-commit hooks):

```bash
# Install runtime + dev dependency group
uv sync --group dev

# Editable install for local changes
uv pip install -e .

# Set up git hooks (optional but recommended)
pre-commit install
```

## Package Layout

```
dojozero/
├─ README.md                Project overview (this file)
├─ design/                  Architecture notes and decision records
├─ tools/                   Utility scripts (data collection, deduplication, etc.)
├─ src/dojozero/            Runtime, core abstractions, and CLI entry points
│  ├─ agents/               Agent implementations
│  ├─ core/                 Dashboard, registry, actor bases, and stores
│  ├─ data/                 Data stream implementations
│  ├─ nba_moneyline/        NBA moneyline betting scenario
│  ├─ ray_runtime/          Optional Ray runtime provider
│  └─ samples/              Reference trial builders (bounded-random, etc.)
└─ tests/                   Pytest suites covering CLI, registry, samples
```

## Authoring New Scenarios

Every scenario (a specific wiring of data streams, operators, and agents)
exposes a *trial builder* that turns serialized configs into a `TrialSpec`.
Builders are registered with a Pydantic `BaseModel` schema so the CLI can
validate inputs, render JSON Schema, and generate ready-to-edit YAML templates
automatically. New built-in scenarios that ship with DojoZero should live inside
the `dojozero` package (for example, `dojozero.samples`).

```python
from pydantic import BaseModel, Field

from dojozero.core import TrialSpec, register_trial_builder


class MyScenarioConfig(BaseModel):
		stream_id: str = Field(default="prices")
		window: int = Field(ge=1, default=10)


def build_my_scenario(trial_id: str, config: MyScenarioConfig) -> TrialSpec:
		# construct AgentSpec / OperatorSpec / DataStreamSpec objects here
		...


register_trial_builder(
		"myenv.prices",
		MyScenarioConfig,
		build_my_scenario,
		description="Example scenario wiring a rolling window strategy",
		example_config=MyScenarioConfig(window=20),
)
```

Once imported, `dojo0 get-builder myenv.prices` will show the schema and can
emit a ready-made YAML spec for local experimentation.

## Tools

Utility scripts for data collection and management are available in the `tools/` directory.
See [docs/nba-trial-runner.md](./docs/nba-trial-runner.md) for documentation on:

- **NBA Trial Runner**: Automated driver for running trials and collecting replay data for NBA games
- **JSONL Deduplication**: Tool for removing duplicate events from replay files (see `tools/deduplicate_jsonl.py`)
