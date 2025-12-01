# AgentX

AgentX is a system for hosting AI agents that run continously on realtime data
to reason about future outcomes and act on them, such as trading and placing bets.

*"AgentX" is a code name for this project, and the public name will be decided in the future.*

> 🚧 This project is in early development. Architecture design and core features are currently being implemented.
> See [Design](./design/) for decision records on design.

## Installation

1. Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
2. Install the package (runtime-only dependencies) from the repo root:

```bash
uv pip install . 
```

This installs AgentX for running trials only. For development workflows (tests,
lint, editable installs), see the [Development Setup](#development-setup)
section below.

## Standalone Usage

AgentX trials start from a params file that names the trial builder and its
inputs. Each builder defines the scenario—the combination of data streams,
operators, and agents that act together during the trial. Use `agentx
list-builders` to see registered scenarios, then run `agentx get-builder
<name> --create-example-params` (or author the params file directly):

```yaml
# sample_trial.yaml
scenario:
	name: samples.bounded-random
	config:
		total_events: 5
		interval_seconds: 0.0
```

Launch the trial by pointing `agentx run` at the params file (add `--trial-id`
to set a friendly identifier, otherwise a UUID is generated):

```bash
agentx run --params sample_trial.yaml --trial-id sample-trial
```

Use `agentx list-builders` to discover registered scenarios (their
data streams, operators, and agents), and add imports with repeated `--import-module`
flags whenever your builder lives outside the defaults.

### Resuming trials

Resume without re-supplying the spec by combining `--trial-id` with either a
checkpoint id or the `--resume-latest` flag:

```bash
# Resume from a known checkpoint
agentx run --trial-id sample-trial --checkpoint-id 3f2c6a9e

# Resume from the latest checkpoint stored for the trial
agentx run --trial-id sample-trial --resume-latest
```

You can still start a new trial from a checkpoint by supplying both `--params`
and `--checkpoint-id`; the CLI applies the checkpoint before launching.

## Server Usage (Coming Soon)

The `agentx serve` command is reserved for a FastAPI dashboard server that will
reuse the existing `Dashboard` runtime. Use the top-level `--setting` flag here
too so the server process can share the same dashboard settings (store/runtime
and import wiring) as `agentx run`. The CLI already exposes placeholder flags (`--host`, `--port`) so
future releases can add the server without breaking backward compatibility.

## Runtime & Store Configuration

When you need persistent dashboard storage, non-default imports, or an
alternative runtime provider, declare those dashboard settings once in
`agentx.yaml` (or any filename you pass to the top-level `--setting` flag).
The settings file captures everything the dashboard runtime needs: store,
runtime provider, and module imports.

```yaml
# agentx.yaml
store:
	kind: filesystem
	root: ./agentx-store
runtime:
	kind: local
imports:
	- agentx.samples
```

Launch with `agentx --setting agentx.yaml run --params sample_trial.yaml` (or the
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

Then run `agentx --setting ray.yaml run --params sample_trial.yaml` to launch the
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
agentx/
├─ README.md                Project overview (this file)
├─ design/                  Architecture notes and decision records
├─ src/agentx/              Runtime, core abstractions, and CLI entry points
│  ├─ core/                 Dashboard, registry, actor bases, and stores
│  ├─ samples/              Reference trial builders (bounded-random, etc.)
│  └─ ray_runtime/          Optional Ray runtime provider
└─ tests/                   Pytest suites covering CLI, registry, samples
```

## Authoring New Scenarios

Every scenario (a specific wiring of data streams, operators, and agents)
exposes a *trial builder* that turns serialized configs into a `TrialSpec`.
Builders are registered with a Pydantic `BaseModel` schema so the CLI can
validate inputs, render JSON Schema, and generate ready-to-edit YAML templates
automatically. New built-in scenarios that ship with AgentX should live inside
the `agentx` package (for example, `agentx.samples`).

```python
from pydantic import BaseModel, Field

from agentx.core import TrialSpec, register_trial_builder


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

Once imported, `agentx get-builder myenv.prices` will show the schema and can
emit a ready-made YAML spec for local experimentation.
