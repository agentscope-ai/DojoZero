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

## Configuration Files

DojoZero uses two types of YAML configuration files:

| File Type | Directory | Purpose | Used With |
|-----------|-----------|---------|-----------|
| **Trial params** | `trial_params/` | Run a single trial for a specific game | `dojo0 run`, `dojo0 backtest` |
| **Trial sources** | `trial_sources/` | Auto-discover games and schedule trials | `dojo0 serve --trial-source` |

**Trial params** define a complete trial specification with a specific `game_id`/`event_id`. Use these for manual trial runs (see [Standalone Usage](#standalone-usage)) or backtesting (see [Backtest Usage](#backtest-usage)).

**Trial sources** define how to discover upcoming games and create trials automatically. The Dashboard Server periodically syncs with sports APIs (NBA/NFL) and schedules trials based on game times. See [Trial Sources (Automatic Scheduling)](#trial-sources-automatic-scheduling).

## Standalone Usage

Trials start from a trial params file that names the trial builder and its
inputs. Each builder defines the scenario—the combination of data streams,
operators, and agents that act together during the trial. Use `dojo0
list-builders` to see registered scenarios, then run `dojo0 get-builder
<name> --create-example-params` (or author the params file directly):

```yaml
# trial_params/nba_trial.yaml
scenario:
  name: nba
  config:
    espn_game_id: "401810490"
    hub:
      persistence_file: "outputs/nba_events.jsonl"
```

Launch the trial by pointing `dojo0 run` at the params file (add `--trial-id`
to set a friendly identifier, otherwise a UUID is generated):

```bash
dojo0 run --params trial_params/sample.yaml --trial-id sample-trial
```

Use `dojo0 list-builders` to discover registered scenarios (their
data streams, operators, and agents). For custom scenarios in external modules,
add the `imports` key to your params file (see [Loading Custom Scenarios](#loading-custom-scenarios)).

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
dojo0 run --params trial_params/nba-moneyline.yaml --trial-id test --server http://localhost:8000
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

# With custom store directory and Ray runtime
dojo0 serve --store-directory ./my-store --runtime-provider ray --ray-config ray_config.yaml --trace-backend jaeger
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
- `--store-directory` - Directory for filesystem store (default: ./dojozero-store)
- `--runtime-provider {local,ray}` - Runtime provider (default: local)
- `--ray-config` - Path to Ray runtime configuration YAML file

### Trial Sources (Automatic Scheduling)

Trial sources enable automatic game discovery and trial scheduling. When the server starts with `--trial-source`, it periodically syncs with sports APIs to find upcoming games and schedules trials to start before each game.

```yaml
# trial_sources/nba.yaml
source_id: nba-moneyline-source
sport_type: nba

config:
  scenario_name: nba-moneyline
  scenario_config:
    # Same structure as trial params, but without game_id
    # (game_id is filled in automatically for each discovered game)
    hub: {}
    data_streams:
      # ... stream definitions
    operators:
      # ... operator definitions
    agents:
      # ... agent definitions

  # Scheduling options
  pre_start_hours: 2.0           # Start trial 2 hours before game
  check_interval_seconds: 60.0   # Check game status every 60 seconds
  auto_stop_on_completion: true  # Stop trial when game finishes
```

Manage trial sources via CLI:
```bash
dojo0 list-sources --server http://localhost:8000
dojo0 list-trials --scheduled --server http://localhost:8000
dojo0 remove-source <source_id> --server http://localhost:8000
dojo0 clear-schedules --server http://localhost:8000
```

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

## Backtest Usage

Run backtesting to replay historical events through agents. This allows testing agent behavior against recorded data.

```bash
# Single file
dojo0 backtest \
  --events outputs/nba_betting_events.jsonl \
  --params trial_params/nba-moneyline.yaml \
  --speed 2.0 \
  --max-sleep 20.0

# Multiple files with glob pattern
dojo0 backtest \
  --events "outputs/2025-01-*/*.jsonl" \
  --params trial_params/nba-moneyline.yaml \
  --speed 2.0

# OSS files (requires oss2 package)
dojo0 backtest \
  --events "oss://bucket/prefix/*.jsonl" \
  --params trial_params/nba-moneyline.yaml

# Submit to Dashboard Server
dojo0 backtest \
  --events outputs/nba_betting_events.jsonl \
  --params trial_params/nba-moneyline.yaml \
  --server http://localhost:8000
```

CLI options:
- `--events` - Path(s) to JSONL event files (supports glob patterns and OSS URLs)
- `--params` - Trial params YAML file
- `--speed` - Playback speed multiplier (default: 1.0 = real-time)
- `--max-sleep` - Maximum sleep between events in seconds (default: 20.0)
- `--trial-id` - Custom trial ID (auto-generated from filename if not specified)
- `--server` - Submit to Dashboard Server instead of running locally

Files are processed sequentially in sorted order. Each file gets a unique trial ID based on the filename.

### Params Usage in Backtest

In backtest mode, events are replayed from the JSONL file rather than fetched live. The params file is still required but certain fields are ignored:

**Ignored fields:**
- `game_id` / `event_id` - Events come from the file, not live sources
- `hub.persistence_file` - Backtest uses the event file for hub configuration
- Data stream `initializer` configs - No live data fetching occurs

**Used fields:**
- `agents` - Agent configurations (IDs, classes, config paths, subscriptions)
- `operators` - Operator configurations
- `data_streams` - Stream definitions for routing events to agents

## Runtime & Store Configuration

Store and runtime settings are configured directly via command-line options for `run`, `backtest`, and `serve` commands:

- `--store-directory` - Directory for the filesystem store (default: `./dojozero-store`)
- `--runtime-provider {local,ray}` - Runtime provider (default: `local`)
- `--ray-config` - Path to Ray configuration YAML file (only used with `--runtime-provider ray`)

Example with custom store directory:
```bash
dojo0 run --params sample_trial.yaml --store-directory ./my-store
```

### Using the Ray runtime

Switch to Ray by setting `--runtime-provider ray` and optionally providing a config file. Install the extras first via `uv pip install ".[ray]"`.

```yaml
# ray_config.yaml
auto_init: false
init_kwargs:
  address: auto
  num_cpus: 8
```

Then run with the Ray runtime:
```bash
dojo0 run --params sample_trial.yaml --runtime-provider ray --ray-config ray_config.yaml
```

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

A scenario defines how data streams, operators, and agents are wired together for a trial. To create a new scenario:

1. **Define a config schema** using Pydantic for validation
2. **Write a builder function** that constructs a `TrialSpec`
3. **Register the builder** so the CLI can discover it

```python
from pydantic import BaseModel, Field
from dojozero.core import TrialSpec, register_trial_builder


class MyScenarioConfig(BaseModel):
    stream_id: str = Field(default="prices")
    window: int = Field(ge=1, default=10)


def build_my_scenario(trial_id: str, config: MyScenarioConfig) -> TrialSpec:
    # Construct AgentSpec / OperatorSpec / DataStreamSpec objects here
    ...


register_trial_builder(
    "myenv.prices",
    MyScenarioConfig,
    build_my_scenario,
    description="Example scenario wiring a rolling window strategy",
    example_config=MyScenarioConfig(window=20),
)
```

Once registered, the CLI provides discovery and scaffolding:

```bash
# List all available builders
dojo0 list-builders

# View schema and generate example params file
dojo0 get-builder myenv.prices --create-example-params
```

### Loading Custom Scenarios

DojoZero automatically imports built-in scenarios (`dojozero.nba`, `dojozero.nfl`). For custom scenarios in external modules, add the `imports` key to your params file:

```yaml
# my_trial.yaml
imports:
  - my_custom_module
scenario:
  name: myenv.prices
  config:
    window: 20
```

## Tools

Utility scripts for data collection and management are available in the `tools/` directory.
See [docs/nba-trial-runner.md](./docs/nba-trial-runner.md) for documentation on:

- **NBA Trial Runner**: Automated driver for running trials and collecting event data for NBA games
- **JSONL Deduplication**: Tool for removing duplicate events from event files (see `tools/deduplicate_jsonl.py`)
