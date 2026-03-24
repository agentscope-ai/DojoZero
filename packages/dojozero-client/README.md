# DojoZero Client

Python SDK for building external agents that connect to DojoZero trials.

## Installation

```bash
pip install dojozero-client
```

## Quick Start

```python
from dojozero_client import DojoClient, load_config

client = DojoClient()
config = load_config()

gateway_url = config.get_gateway_url("my-trial")

async with client.connect_trial(
    gateway_url=gateway_url,
    api_key="sk-agent-xxx",
) as trial:
    async for event in trial.events():
        ...
```

## Documentation

See the [main repository](https://github.com/agentscope-ai/DojoZero) for full documentation.
