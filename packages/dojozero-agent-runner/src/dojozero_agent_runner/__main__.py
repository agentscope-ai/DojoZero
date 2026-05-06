"""Entry point: ``python -m dojozero_agent_runner`` / ``dojozero-agent-runner``.

Three ways to configure:

  1. **CLI flags** (most explicit; preferred for local + ad-hoc launches)::

         python -m dojozero_agent_runner \\
             --portal-zip /path/to/portal-degen.zip \\
             --persona degen --llm qwen-max \\
             --trial-id <id> --gateway-url http://localhost:8080

  2. **Environment variables** (k8s-pod style; default fallback)::

         DOJOZERO_PERSONA=degen DOJOZERO_LLM=qwen-max ... \\
             python -m dojozero_agent_runner

  3. **Programmatic** — call :func:`dojozero_agent_runner.run`
     with a :class:`RunnerConfig` directly. Useful when one process
     orchestrates multiple runners.

CLI flags override environment variables when both are set.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dojozero-agent-runner",
        description=(
            "Run a DojoZero external agent against a trial. Each flag "
            "may be omitted; the runner falls back to the equivalent "
            "DOJOZERO_* / AGENTID_* environment variable."
        ),
    )
    parser.add_argument(
        "--persona",
        default=None,
        help="Persona name (env: DOJOZERO_PERSONA). Maps to a YAML in agents/personas/.",
    )
    parser.add_argument(
        "--llm",
        default=None,
        help="LLM model_display_name (env: DOJOZERO_LLM). Looked up in agents/llms/default.yaml.",
    )
    parser.add_argument(
        "--trial-id",
        default=None,
        help="Trial to join (env: DOJOZERO_TRIAL_ID).",
    )
    parser.add_argument(
        "--dashboard-url",
        default=None,
        help=(
            "Dashboard server base URL (env: DOJOZERO_DASHBOARD_URL). "
            "The trial's gateway URL is derived as "
            "<dashboard-url>/api/trials/<trial-id>. Preferred for "
            "production / dashboard-mode. Mutually exclusive with --gateway-url."
        ),
    )
    parser.add_argument(
        "--gateway-url",
        default=None,
        help=(
            "Trial gateway base URL (env: DOJOZERO_GATEWAY_URL). Use this "
            "for standalone `dojo0 serve --trial-id ...` where the gateway "
            "is at root. Mutually exclusive with --dashboard-url."
        ),
    )
    parser.add_argument(
        "--persona-path",
        default=None,
        help="Override persona YAML path (env: DOJOZERO_PERSONA_PATH).",
    )
    parser.add_argument(
        "--llm-config-path",
        default=None,
        help="Override LLM matrix YAML path (env: DOJOZERO_LLM_CONFIG_PATH).",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=None,
        help="Event-poll cadence (env: DOJOZERO_POLL_INTERVAL_SECONDS, default 5).",
    )
    parser.add_argument(
        "--agentid-audience",
        default=None,
        help=(
            "AgentID JWT 'aud' claim override "
            "(env: DOJOZERO_AGENTID_AUDIENCE; default = gateway-url)."
        ),
    )
    parser.add_argument(
        "--portal-zip",
        default=None,
        help=(
            "Path to a portal-agent.zip containing agent.json + private_key. "
            "When set, takes precedence over --agent-profile and AGENTID_* env vars."
        ),
    )
    parser.add_argument(
        "--agent-profile",
        default=None,
        help=(
            "Name of an agent created by `agent-id-cli agent create --name X`. "
            "Loaded from $AGENTID_HOME/agents/<name>/ (default ~/.agentid/agents/<name>/). "
            "Mutually exclusive with --portal-zip; both override AGENTID_* env."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry: parse args, build config (CLI > env), run, return exit code."""
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Imports are local so ``--help`` and config errors don't pay the
    # agentscope/dojozero-client import cost upfront.
    from dojozero_agent_runner._config import load_config_from_args
    from dojozero_agent_runner._runner import run

    try:
        config = load_config_from_args(args)
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
