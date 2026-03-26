# DojoZero

A platform for running AI agents on realtime sport data and make predictions about game outcomes.

## Installation

```bash
pip install dojozero
```

### Optional extras

```bash
pip install dojozero[alicloud]   # Alibaba Cloud integration (OSS, credentials, SLS)
pip install dojozero[redis]      # Redis-backed data stores
pip install dojozero[ray]        # Distributed execution via Ray
```

## Environment Setup

```bash
cp .env.example .env
```

## Quick Start

### Run a single trial locally

```bash
dojo0 run --params trial_params/nba-moneyline.yaml --trial-id nba-local-001
```

### Resume an interrupted trial

```bash
dojo0 run --trial-id nba-local-001 --resume-latest
```

### Run through dashboard server

```bash
# Start server with tracing
dojo0 serve --trace-backend jaeger

# Submit a trial from another terminal
dojo0 run \
  --params trial_params/nba-moneyline.yaml \
  --trial-id nba-server-001 \
  --server http://localhost:8000
```

### Automatic scheduling with trial sources

```bash
dojo0 serve --trace-backend jaeger --trial-source "trial_sources/daily/*.yaml"
```

### Backtest from captured events

```bash
dojo0 backtest \
  --events outputs/2026-01-12/401772976.jsonl \
  --params outputs/2026-01-12/401772976.yaml \
  --speed 100 --max-sleep 1
```

### Arena (live visualization)

```bash
dojo0 arena --trace-backend jaeger
```

## Command Index

| Command | Purpose |
|---------|---------|
| `dojo0 run` | Start a local or server-submitted trial |
| `dojo0 serve` | Dashboard / orchestration server |
| `dojo0 arena` | Trace-backed Arena visualization server |
| `dojo0 backtest` | Replay persisted event streams |
| `dojo0 list-sources` | List trial sources |
| `dojo0 list-trials` | List scheduled trials |
| `dojo0 clear-schedules` | Clear scheduled runs |

## Documentation

- [Documentation](https://github.com/agentscope-ai/DojoZero/docs/README.md)

## License

MIT
