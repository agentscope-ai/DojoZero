"""Entry point: ``python -m dojozero_agent_runner`` / ``dojozero-agent-runner``."""

from __future__ import annotations

import asyncio
import logging
import sys


def main() -> int:
    """CLI entry: load config from env, run the agent, return exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Imports are local so ``--help`` and config errors don't pay the
    # agentscope/dojozero-client import cost upfront.
    from dojozero_agent_runner._config import load_config_from_env
    from dojozero_agent_runner._runner import run

    try:
        config = load_config_from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        logging.exception("Runner exited with error: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
