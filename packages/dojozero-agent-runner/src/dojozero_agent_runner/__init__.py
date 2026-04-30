"""DojoZero Agent Runner — single-image external agent.

Public API:

    from dojozero_agent_runner import RunnerConfig, run

The runner is normally launched via the ``dojozero-agent-runner`` console
script (``python -m dojozero_agent_runner``); the public API exists so
tests and embedding code paths can construct a runner programmatically.
"""

from dojozero_agent_runner._config import RunnerConfig, load_config_from_env

__all__ = ["RunnerConfig", "load_config_from_env"]
