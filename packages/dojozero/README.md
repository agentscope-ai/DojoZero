# DojoZero

A platform for running AI agents on realtime sport data to reason about game outcomes and make predictions. DojoZero provides an actor-based runtime with live data streams, agent orchestration, trial management, backtesting, and tracing. Currently supports NBA, NFL, and NCAA.

## Installation

```bash
pip install dojozero
```

Optional extras:

```bash
pip install dojozero[alicloud]   # Alibaba Cloud integration (OSS, SLS tracing)
pip install dojozero[redis]      # Redis-backed data stores
pip install dojozero[ray]        # Distributed execution via Ray
```

## Documentation

See the [full documentation](https://github.com/agentscope-ai/DojoZero/tree/main/docs) for setup, configuration, and usage guides.

## License

MIT
