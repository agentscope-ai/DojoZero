# Single Trial Execution

Use this guide to:
- Run a new trial from a trial configuration
- Resume a stopped trial from checkpoint state
- Configure trial parameters and agent personas

## 1. Run a New Trial

Run `dojo0` with a trial params file (typically under `trial_params/`) and an optional trial ID:

```bash
dojo0 run --params trial_params/nba-moneyline.yaml --trial-id nba-local-001
```

If you omit `--trial-id`, DojoZero generates a UUID automatically.

## 2. Resume an Interrupted Trial

If a trial stops unexpectedly, resume it with the same trial ID.

Resume from a specific checkpoint:

```bash
dojo0 run --trial-id nba-local-001 --checkpoint-id <checkpoint_id>
```

Resume from the latest checkpoint:

```bash
dojo0 run --trial-id nba-local-001 --resume-latest
```

## 3. Trial Configuration

### Trial Params Files (`trial_params/*.yaml`)

A params file defines the full configuration for a single trial.
Example NBA scenario:

```yaml
# trial_params/nba-moneyline.yaml
scenario:
  name: nba
  config:
    espn_game_id: "401810854"  # Target ESPN game ID
    hub:
      persistence_file: outputs/nba_prediction_events-{espn_game_id}.jsonl
    data_streams:
      # ... stream configurations
    operators:
      # ... operator configurations
    agents:
      # ... agent configurations
```

### Configuration Reference

| Field | Purpose |
| :--- | :--- |
| `scenario.name` | Registered builder name (for example: `nba`, `nfl`, or a custom builder). |
| `scenario.config.espn_game_id` | Target ESPN game/event identifier. |
| `scenario.config.hub.persistence_file` | JSONL output path for persisted events. |
| `scenario.config.data_streams` | Data stream definitions and event subscriptions. |
| `scenario.config.operators` | Operator instances and tool exposure settings. |
| `scenario.config.agents` | Agent definitions, including personas, model settings, and subscriptions. |


## 4. Agent Configuration

- **Persona config** (`agents/personas/*.yaml`): strategy style, tone, risk profile, and decision preferences.
- **LLM config** (`agents/llms/*.yaml`): model provider, model name, and API key environment mapping.

### Persona configuration

Persona files define how an agent reasons and acts. Typical fields include:

- Role/identity text (what type of predictor/analyst the agent is)
- Risk appetite and bankroll behavior
- Decision heuristics and tool-usage style
- Output format conventions for consistency

You can create multiple personas and compare them under the same trial to evaluate behavioral differences.

### LLM provider configuration

LLM config files map agents to one or more providers/models.

Example:
- `agents/llms/default.yaml`


## 5. What a Trial Produces

When a trial runs, DojoZero writes a JSONL event log to the path specified by `hub.persistence_file` in your trial params (by default, under `outputs/`). This file contains every event that flowed through the trial: game state updates, odds changes, agent decisions, predictions, and results.

```
outputs/nba_prediction_events-401810854.jsonl
```

This event log is the primary artifact of a trial run. You can use it to:

- **Backtest** different agent configurations against the same game data (see [Backtesting](./backtesting.md))
- **Debug** agent behavior by inspecting the event sequence
- **Evaluate** prediction accuracy and strategy performance

> **Tip: How do I see what's happening during a trial?**
> The terminal output shows high-level progress, but for detailed visibility into agent reasoning, event flow, and decision traces, set up tracing. See [Tracing](./tracing.md) for setup, and [Arena](./arena.md) for a browser-based timeline view. You can enable tracing on a single trial with `dojo0 run --trace-backend jaeger`.

## 6. Find the Next Playable Game

Use the trial runner to list upcoming games:

```bash
python tools/nba_trial_runner.py list
```

For complete runner options, see [`trial_runner.md`](./trial_runner.md).

## What's Next

- **Scale up**: When you're ready to run multiple trials or schedule them automatically, use the [Dashboard Server](./dashboard_server.md). The dashboard provides a central service that manages trial lifecycle, supports auto-scheduling from sports APIs, and gives you a single place to monitor all running trials.
- **Iterate offline**: Replay your trial's event log through different agent configurations with [Backtesting](./backtesting.md) — no need to wait for a live game.
- **Observe in detail**: Set up [Tracing](./tracing.md) to capture OpenTelemetry spans for every agent decision, then explore them in the [Arena UI](./arena.md).
