"""Entry point: ``python -m dojozero_agent_runner`` / ``dojozero-agent-runner``.

Identity (durable) and brain config (iteration-friendly) are decoupled.
The runner takes one of each:

  Identity sources (mutually exclusive):
    --agent-profile <name>   identity from $AGENTID_HOME/agents/<name>/
    --agent-zip <path>       identity from an agent zip (e.g. portal export)
    (none)                   AGENTID_* env vars (k8s pod default)

  Brain sources:
    --agent-brain <path>    one YAML with sys_prompt + model: block
    --agent-prompt <path> + --agent-model <path>   two-file split
    (both)                   piecewise overrides individual fields

Examples::

    # Profile identity + agent brain (the common shape)
    python -m dojozero_agent_runner \\
        --agent-profile cli-agent \\
        --agent-brain ./agent-brains/dojozero-degen-claude.yaml \\
        --trial-id <id> --dashboard-url http://localhost:8080

    # Same identity, different brain (A/B test)
    python -m dojozero_agent_runner \\
        --agent-profile cli-agent \\
        --agent-brain ./agent-brains/dojozero-whale-qwen.yaml \\
        --trial-id <id> --dashboard-url http://localhost:8080

    # Ad-hoc smoke test (zip identity, piecewise brain)
    python -m dojozero_agent_runner \\
        --agent-zip /path/to/agent.zip \\
        --agent-prompt ./prompt.md --agent-model ./model.yaml \\
        --trial-id <id> --dashboard-url http://localhost:8080

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

    # --- trial / transport ---
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

    # --- identity sources (mutually exclusive; env fallback if all unset) ---
    parser.add_argument(
        "--agent-profile",
        default=None,
        help=(
            "Name of an agent profile under $AGENTID_HOME/agents/<name>/ "
            "(default ~/.agentid/agents/<name>/). Identity-only — supplies "
            "agent.json + private_key. Brain config is provided separately "
            "via --agent-brain. Env: DOJOZERO_AGENT_PROFILE."
        ),
    )
    parser.add_argument(
        "--agent-zip",
        default=None,
        help=(
            "Path to an agent zip (e.g. portal export) containing agent.json "
            "+ private_key. Identity-only — pair with --agent-brain (or "
            "--sys-prompt-file + --model-config) to supply the brain. "
            "Env: DOJOZERO_AGENT_ZIP."
        ),
    )

    # --- brain config (decoupled from --agent-profile) ---
    parser.add_argument(
        "--agent-brain",
        default=None,
        help=(
            "Path to an agent-brain YAML (sys_prompt + model: block). "
            "The canonical shape produced by `dojo0 agents build-brains`. "
            "Env: DOJOZERO_AGENT_BRAIN."
        ),
    )
    parser.add_argument(
        "--agent-prompt",
        default=None,
        help=(
            "Path to a file containing the system prompt (raw text). "
            "Two-file alternative to --agent-brain; overrides the "
            "sys_prompt field when both are given. "
            "Env: DOJOZERO_AGENT_PROMPT."
        ),
    )
    parser.add_argument(
        "--agent-model",
        default=None,
        help=(
            "Path to a YAML file with the model config "
            "(model_type, model_name, api_key_env, ...). Two-file "
            "alternative to --agent-brain; overrides the model block "
            "when both are given. Env: DOJOZERO_AGENT_MODEL."
        ),
    )

    # --- misc ---
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
            "(env: DOJOZERO_AGENTID_AUDIENCE; default = dashboard or gateway URL)."
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
