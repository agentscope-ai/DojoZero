# DojoZero

AI agent system for real-time data reasoning and automated prediction/trading. Agents run continuously on live data streams to analyze outcomes and take actions.

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

## Quick Start

```bash
# List available trial types
dojo0 list

# Run a trial
dojo0 run <trial-type> --config config.yaml

# Backtest from captured events
dojo0 backtest --events events.jsonl --params params.yaml
```

## Documentation

See the [main repository](https://github.com/agentscope-ai/DojoZero) for full documentation.
