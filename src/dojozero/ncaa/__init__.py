"""NCAA betting module with DataHub integration.

This module provides NCAA-specific betting scenario components that build on
the shared betting infrastructure from `dojozero.betting`.

The trial builder is automatically registered when importing this module.
"""

# Import trial module for side effect (registers trial builder)
import dojozero.ncaa._trial  # noqa: F401

__all__: list[str] = []
