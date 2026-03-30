# Backtesting

Backtesting replays historical event files through your agent stack so you can evaluate behavior quickly and reproducibly.

## Where Do Event Files Come From?

Every trial writes a JSONL event log to the path configured in `hub.persistence_file` (typically under `outputs/`). This file records every event that flowed through the trial — game state updates, odds changes, agent messages, predictions, and results.

For example, after running a trial with the default NBA config, you'll find:

```
outputs/nba_prediction_events-401810854.jsonl
```

This is the file you pass to `--events` below. You can also replay event files from other trials, or use glob patterns to backtest across multiple games at once.

> **Tip:** To see detailed agent behavior during a backtest, enable tracing with `--trace-backend jaeger`. See [Tracing](./tracing.md) for setup.

## 1. Basic Usage

```bash
dojo0 backtest \
  --events outputs/nba_prediction_events_{espn_game_id}.jsonl \
  --params trial_params/nba-moneyline.yaml \
  --speed 2.0 \
  --max-sleep 20.0
```

## 2. Replay Multiple Files (Glob)

```bash
dojo0 backtest \
  --events "outputs/2026-03-*/*.jsonl" \
  --params trial_params/nba-moneyline.yaml \
  --speed 5.0
```

Files are processed in sorted order.

## 3. Submit Backtest to Dashboard Server

```bash
dojo0 backtest \
  --events outputs/nba_prediction_events.jsonl \
  --params trial_params/nba-moneyline.yaml \
  --server http://localhost:8000
```

Use `--server` when you want orchestration and visibility through the dashboard service.

## 4. CLI Options

| Option | Description |
|---|---|
| `--events` | JSONL file(s), supports glob patterns |
| `--params` | Trial params YAML used to build agent/operator graph |
| `--speed` | Playback multiplier (`1.0` = real-time) |
| `--max-sleep` | Maximum delay between events during replay |
| `--trial-id` | Custom trial ID |
| `--server` | Submit to dashboard instead of local process |
| `--store-directory` | Store/checkpoint root |
| `--runtime-provider` | `local` or `ray` |
| `--ray-config` | Ray runtime config file |

## What's Next

- **Inspect agent reasoning**: Enable tracing during backtests (`--trace-backend jaeger`) and explore the results in [Arena](./arena.md) to see exactly how agents responded to each event.
- **Compare strategies**: Run the same event file with different agent personas or LLM configurations and compare outcomes. See [Configuration](./configuration.md) for persona and LLM config options.
- **Scale up**: Submit backtests to the [Dashboard Server](./dashboard_server.md) with `--server` for centralized tracking alongside live trials.
