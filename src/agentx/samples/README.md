# Samples

This directory contains sample scenarios and examples for AgentX.

## Structure

- `bounded_random.py` - Basic bounded random scenario example
- `bounded_random_buffered.py` - Buffered version of bounded random scenario
- `data/_google_search_example.py` - Example demonstrating Google Search → LLM Processing → SearchResultFact flow

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
```

## Note

Samples are separate from the main `agentx` package source code. They demonstrate:
- How to create actor configurations
- How to use runtime context
- How to implement checkpoint-friendly state
- How to register trial builders

