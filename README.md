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

### Backtest Mode

Run backtesting from historical event files:

```bash
dojo0 backtest \
  --events outputs/nba_betting_events.jsonl \
  --params configs/nba-moneyline.yaml \
  --speed 2.0 \
  --max-sleep 20.0
```

## Server Usage

The Dashboard Server (`dojo0 serve`) manages trials and exports traces via OTLP. Submit trials to a running server using the `--server` flag with `dojo0 run` or `dojo0 backtest`.

```bash
# 1. Start Jaeger (trace store)
docker run -d --name jaeger \
  -p 16686:16686 -p 4317:4317 -p 4318:4318 \
  jaegertracing/all-in-one:latest

# 2. Start Dashboard Server (default: 127.0.0.1:8000)
dojo0 serve --trace-backend jaeger

# 3. Submit a trial (in another terminal)
dojo0 run --params configs/nba-moneyline.yaml --trial-id test --server http://localhost:8000

# Or submit a backtest trial
dojo0 backtest --params configs/nba-moneyline.yaml --events outputs/nba_betting_events.jsonl \
  --trial-id backtest-test --speed 1.0 --max-sleep 20 --server http://localhost:8000
```

### Dashboard Server Options

```bash
# With SLS trace backend (production - Alibaba Cloud)
dojo0 serve --trace-backend sls

# With OSS backup for event data
dojo0 serve --trace-backend sls --oss-backup

# With trial sources for automatic scheduling (supports glob patterns)
dojo0 serve --trace-backend jaeger --trial-source "trial_sources/*.yaml"

# Disable auto-resume of interrupted trials
dojo0 serve --trace-backend jaeger --no-auto-resume

# With settings file
dojo0 --setting dojozero.yaml serve --trace-backend jaeger
```

CLI options:
- `--host` - Host address (default: 127.0.0.1)
- `--port` - Port (default: 8000)
- `--trace-backend {jaeger,sls}` - Trace backend type
- `--trace-ingest-endpoint` - OTLP endpoint for Jaeger (default: http://localhost:4318)
- `--service-name` - Service name for traces (default: dojozero)
- `--oss-backup` - Enable OSS backup (requires `DOJOZERO_OSS_BUCKET`, `DOJOZERO_OSS_ENDPOINT`)
- `--trial-source` - Path or glob pattern for trial source YAML files (repeatable)
- `--no-auto-resume` - Disable automatic resuming of interrupted trials
- `--stale-threshold-hours` - Skip resuming trials with checkpoints older than this (default: 24.0)

### Auto-Resume of Interrupted Trials

By default, the Dashboard Server automatically resumes trials that were running when the server previously shut down. This requires a persistent store (see [Runtime & Store Configuration](#runtime--store-configuration)). On startup, the server:

1. Scans the store for trials with RUNNING or STARTING status
2. Checks if each trial has a checkpoint available
3. Resumes trials from their latest checkpoint

Trials without checkpoints cannot be safely resumed and are marked as FAILED. Trials with checkpoints older than the stale threshold (default: 24 hours) are skipped.

To disable this behavior, use `--no-auto-resume`. To adjust the staleness threshold, use `--stale-threshold-hours`.

### SLS Configuration (Production)

For Alibaba Cloud deployments, set these environment variables:

```bash
export DOJOZERO_SLS_PROJECT="your-project"
export DOJOZERO_SLS_ENDPOINT="cn-hangzhou.log.aliyuncs.com"
export DOJOZERO_SLS_LOGSTORE="dojozero-traces"
```

Credentials are resolved via env vars, `~/.alibabacloud/credentials`, or ECS RAM role.

## Arena

The Arena Server (`dojo0 arena`) serves the web UI and streams real-time data to browsers via WebSocket.

### Arena Server Options

```bash
# With SLS as trace source (production)
dojo0 arena --trace-backend sls

# Production deployment (serves both API and frontend, default: 127.0.0.1:3001)
cd frontend && npm run build
dojo0 arena --trace-backend sls --static-dir ./frontend/dist
```

CLI options:
- `--host` - Host address (default: 127.0.0.1)
- `--port` - Port (default: 3001)
- `--trace-backend {jaeger,sls}` - Trace backend type (required)
- `--trace-query-endpoint` - Jaeger Query API endpoint (default: http://localhost:16686)
- `--service-name` - Service name for trace queries (default: dojozero)
- `--static-dir` - Path to built static assets (for production)

### UI Development

```bash
cd frontend
npm install      # First time only
npm run dev      # Start dev server at http://localhost:5173
```

Ensure Arena Server is running at `http://localhost:3001`.

## Runtime & Store Configuration

When you need persistent storage, non-default imports, or an
alternative runtime provider, declare those settings once in
`dojozero.yaml` (or any filename you pass to the top-level `--setting` flag).
The settings file captures everything the trial orchestrator needs: store,
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

- **NBA Trial Runner**: Automated driver for running trials and collecting event data for NBA games
- **JSONL Deduplication**: Tool for removing duplicate events from event files (see `tools/deduplicate_jsonl.py`)
