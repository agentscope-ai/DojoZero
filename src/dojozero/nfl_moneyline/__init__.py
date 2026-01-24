"""NFL moneyline betting module with DataHub integration.

This module provides NFL-specific betting scenario components that build on
the shared betting infrastructure from `dojozero.betting`.

The trial builder is automatically registered when importing this module.
Use: uv run dojozero run --params trial_params/nfl-moneyline.yaml

For BrokerOperator, import directly from dojozero.betting:
    from dojozero.betting import BrokerOperator
"""

# Import trial module for side effect (registers trial builder)
import dojozero.nfl_moneyline._trial  # noqa: F401

__all__: list[str] = []
