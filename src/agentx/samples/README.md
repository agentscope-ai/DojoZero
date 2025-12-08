# Samples

This directory contains sample scenarios and examples for AgentX.

## Structure

- `bounded_random.py` - Basic bounded random scenario example
- `bounded_random_buffered.py` - Buffered version of bounded random scenario
- `data/_google_search_example.py` - Example demonstrating Google Search → LLM Processing → SearchResultFact flow
- `data/_nba_api_example.py` - Example demonstrating NBA API → Data Streamer → Aggregation → Facts flow

## Usage

These samples are registered as trial builders and can be used with the CLI:

```bash
# Run a sample trial
agentx run --params sample_trial.yaml --trial-id sample-trial
```

## Import

When running from the project root, samples can be imported as:

```python
import agentx.samples
from agentx.samples.bounded_random import BoundedRandomTrialParams

# Data-related examples are in the data subdirectory
from agentx.samples.data._google_search_example import example_google_search_flow
from agentx.samples.data._nba_api_example import example_nba_api_data_streamer, example_aggregation_to_facts
```

## Note

Samples are separate from the main `agentx` package source code. They demonstrate:
- How to create actor configurations
- How to use runtime context
- How to implement checkpoint-friendly state
- How to register trial builders

