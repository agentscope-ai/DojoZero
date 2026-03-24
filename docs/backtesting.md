# Backtesting Guide

Backtesting replays historical event files through your agent stack so you can evaluate behavior quickly and reproducibly.

## Basic Usage

```bash
dojo0 backtest \
  --events outputs/nba_prediction_events_{espn_game_id}.jsonl \
  --params trial_params/nba-moneyline.yaml \
  --speed 2.0 \
  --max-sleep 20.0
```

## Multiple Files (Glob)

```bash
dojo0 backtest \
  --events "outputs/2025-01-*/*.jsonl" \
  --params trial_params/nba-moneyline.yaml \
  --speed 5.0
```

Files are processed in sorted order.

## Submit Backtest to Dashboard Server

```bash
dojo0 backtest \
  --events outputs/nba_prediction_events.jsonl \
  --params trial_params/nba-moneyline.yaml \
  --server http://localhost:8000
```

## CLI Options

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
