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

## 3. Replay from SLS (Alibaba Cloud Log Service)

Replay a trial directly from its trace stored in SLS, without needing a local JSONL file:

```bash
dojo0 backtest \
  --events sls://nba-game-401869194-25bc3d74 \
  --params trial_params/nba-moneyline.yaml \
  --speed 10 \
  --max-sleep 5
```

The `sls://` prefix triggers fetching the trial's event spans from SLS by trace ID. Events are materialized to a local cache (`outputs/` by default) and reused on subsequent runs.

If a trace contains multiple runs (e.g., a double-submitted trial), pick a specific run with `@run_id`:

```bash
dojo0 backtest \
  --events "sls://<trace_id>@<run_id>" \
  --params trial_params/nba-moneyline.yaml
```

Required env vars for SLS: `DOJOZERO_SLS_PROJECT`, `DOJOZERO_SLS_ENDPOINT`, `DOJOZERO_SLS_LOGSTORE`, plus Alibaba Cloud credentials.

The `espn_game_id` is auto-detected from the first event in the materialized file, so you can use a generic params YAML without specifying the game ID.

## 4. Submit Backtest to Dashboard Server

```bash
dojo0 backtest \
  --events outputs/nba_prediction_events.jsonl \
  --params trial_params/nba-moneyline.yaml \
  --server http://localhost:8000
```

Use `--server` when you want orchestration and visibility through the dashboard service.

## 5. CLI Options

| Option | Description |
|---|---|
| `--events` | JSONL file(s), glob patterns, OSS URLs (`oss://`), or SLS trace IDs (`sls://`) |
| `--params` | Trial params YAML used to build agent/operator graph |
| `--speed` | Playback multiplier (`1.0` = real-time, default `1.0`) |
| `--max-sleep` | Maximum delay between events during replay (default `30.0`) |
| `--emit-traces` | Emit data events to trace backend with rebased timestamps |
| `--trial-id` | Custom trial ID (auto-generated from file name if omitted) |
| `--server` | Submit to dashboard instead of local process |
| `--store-directory` | Store/checkpoint root (default `./dojozero-store`) |
| `--runtime-provider` | `local` or `ray` |
| `--ray-config` | Ray runtime config file |

## 6. Output

After a backtest completes, results are written to:

```
dojozero-store/trials/{trial_id}/
├── spec.json              # Trial specification
├── status.json            # Final trial status (all actors stopped)
├── checkpoint_index.json  # Checkpoints (populated on Ctrl+C graceful shutdown)
└── result.json            # Broker statistics per agent (balance, bets, ROI)
```

The cached event file (from SLS or OSS) is stored in `outputs/` and reused on re-runs.

## What's Next

- **Inspect agent reasoning**: Enable tracing during backtests (`--trace-backend jaeger`) and explore the results in [Arena](./arena.md) to see exactly how agents responded to each event.
- **Compare strategies**: Run the same event file with different agent personas or LLM configurations and compare outcomes. See [Configuration](./configuration.md) for persona and LLM config options.
- **Scale up**: Submit backtests to the [Dashboard Server](./dashboard_server.md) with `--server` for centralized tracking alongside live trials.
