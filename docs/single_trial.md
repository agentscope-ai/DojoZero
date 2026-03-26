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


## 5. Find the Next Playable Game

Use the trial runner to list upcoming games:

```bash
python tools/nba_trial_runner.py list
```

For complete runner options, see [`trial_runner.md`](./trial_runner.md).
