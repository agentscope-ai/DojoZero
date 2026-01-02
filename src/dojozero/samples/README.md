# Samples

This directory contains sample scenarios and examples for DojoZero.

## Structure

- `bounded_random.py` - Basic bounded random scenario example
- `bounded_random_buffered.py` - Buffered version of bounded random scenario

## Usage

These samples are registered as trial builders and can be used with the CLI:

```bash
# Run a sample trial
dojo0 run --params sample_trial.yaml --trial-id sample-trial
```

## Import

When running from the project root, samples can be imported as:

```python
import dojozero.samples
from dojozero.samples.bounded_random import BoundedRandomTrialParams
```

## Note

Samples are separate from the main `dojozero` package source code. They demonstrate:
- How to create actor configurations
- How to use runtime context
- How to implement checkpoint-friendly state
- How to register trial builders

