"""NBA moneyline betting module with DataHub integration.

This module provides NBA-specific betting scenario components that build on
the shared betting infrastructure from `dojozero.betting`.

The trial builder is automatically registered when importing this module.
Use: uv run dojozero run --params configs/nba-moneyline.yaml

For BrokerOperator, import directly from dojozero.betting:
    from dojozero.betting import BrokerOperator
"""

# Import trial module for side effect (registers trial builder)
import dojozero.nba_moneyline._trial  # noqa: F401

__all__: list[str] = []
