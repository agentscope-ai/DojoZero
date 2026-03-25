# Dashboard Server

Use the dashboard server to run, schedule, and monitor trials from a central service.

## 1. Run via Dashboard Server

```bash
# Terminal 1
dojo0 serve

# Terminal 2
dojo0 run \
  --params trial_params/nba-moneyline.yaml \
  --trial-id nba-server-001 \
  --server http://localhost:8000
```

## 2. Scheduling with Trial Sources

```bash
dojo0 serve --trial-source "trial_sources/daily/*.yaml"
dojo0 list-sources
dojo0 list-trials
dojo0 remove-source <source_id>
dojo0 clear-schedules
```

## 3. Trial Source Parameters

`trial_sources/*.yaml` define what the dashboard server should discover (games) and how it should schedule trials for them.

At a minimum, most trial source files include:

| Field | Purpose |
|---|---|
| `source_id` | Stable ID for the discovery source (used by `list-sources` / `remove-source`). |
| `sport_type` | Which league adapter to use (for example `nba` or `nfl`). |
| `config` | Trial/template configuration applied to discovered games. |

Inside `config`, your template can be either:
- **Full template style**: include `scenario_name` and a full `scenario_config` (includes streams/operators/agent wiring).
- **Matrix/shortcut style**: provide `scenario_name` plus higher-level selections like `max_daily_games`, `personas`, and `llm_config_path` (the server uses base templates to expand these into a runnable trial for each discovered game).

Scheduling knobs (when present in your template) typically include:

| Field | Purpose |
|---|---|
| `pre_start_hours` | Start a trial this many hours before game start. |
| `check_interval_seconds` | How often to re-check game status / market readiness. |
| `auto_stop_on_completion` | Stop the trial when the game finishes. |
| `data_dir` | Output directory root for persistence artifacts. |
| `sync_interval_seconds` | How often to sync discovery data from the league API. |

## 4. Runtime and Storage Options

- `--store-directory`: trial state and checkpoint root.
- `--runtime-provider {local,ray}`: execution backend.
- `--ray-config`: Ray initialization YAML file.
