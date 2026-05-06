"""Runner configuration loaded from environment variables and YAML files.

The runner is a single image, parameterized entirely by env. The same image
runs any persona × LLM combination — N processes, one image.

Authentication is AgentID-only. The agent identity (``AGENTID_AGENT_ID``,
``AGENTID_AGENT_KID``, ``AGENTID_PRIVATE_KEY``, optional ``AGENTID_IDP_URL``)
is read by ``agent-id-client-sdk``'s ``Identity.from_env()``; the runner just
verifies the required keys are present at config-load time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Default mounted locations for the runner image. Match the layout in the
# DojoZero / DojoZeroDeploy repos so ``DOJOZERO_PERSONA=degen`` works without
# needing path overrides in the common case.
_DEFAULT_PERSONA_DIR = Path("agents/personas")
_DEFAULT_LLM_CONFIG_PATH = Path("agents/llms/default.yaml")


@dataclass(frozen=True)
class RunnerConfig:
    """Resolved runner configuration.

    Three ways to build:
      - :func:`load_config_from_env` — k8s pod / single image style
      - :func:`load_config_from_args` — CLI flags, optionally with env fallback
      - constructed directly — programmatic / agentic orchestration

    Holds everything needed to wire up the SDK transport, the LLM, and
    the persona prompt — no further env reads happen after this is
    constructed unless ``identity`` is None (in which case the SDK's
    ``Identity.from_env()`` runs at start time).
    """

    persona: str
    llm: str
    trial_id: str
    gateway_url: str
    sys_prompt: str
    llm_config: dict[str, Any]
    poll_interval_seconds: float
    agentid_audience: str | None  # None → defaults to ``gateway_url``
    persona_path: Path
    llm_config_path: Path
    # Pre-loaded AgentID identity. When None, the runner falls back to
    # ``Identity.from_env()`` at start time (k8s pod default).
    # Programmatic / CLI callers populate this — typically via
    # ``Identity.from_zip(<portal-agent.zip>)`` — so identity material
    # doesn't have to be unpacked into env vars first.
    identity: Any = None


def build_config(
    *,
    persona: str,
    llm: str,
    trial_id: str,
    gateway_url: str | None = None,
    dashboard_url: str | None = None,
    persona_path: Path | None = None,
    llm_config_path: Path | None = None,
    poll_interval_seconds: float = 5.0,
    agentid_audience: str | None = None,
    identity: Any = None,
) -> RunnerConfig:
    """Build a :class:`RunnerConfig` from explicit values.

    The programmatic core. Both :func:`load_config_from_env` and
    :func:`load_config_from_args` reduce to this. Callers orchestrating
    runners directly (e.g., a launcher spawning multiple agents) should
    prefer this entry point.

    Args:
        persona: persona name (``degen``, ``whale``, ...).
        llm: ``model_display_name`` from the LLM matrix YAML. Match is
            case-insensitive.
        trial_id: trial to join.
        gateway_url: base URL of the trial's gateway. Use this when
            running against a standalone ``dojo0 serve --trial-id ...``
            (gateway at root). Mutually exclusive with ``dashboard_url``.
        dashboard_url: base URL of the dashboard server. The trial's
            gateway URL is derived as
            ``{dashboard_url}/api/trials/{trial_id}`` — the path the
            dashboard's gateway-router exposes per-trial sub-apps at.
            Use this for production / dashboard-mode deployments.
            Mutually exclusive with ``gateway_url``.
        persona_path: override path to persona YAML; defaults to
            ``agents/personas/{persona}.yaml``.
        llm_config_path: override path to LLM matrix YAML; defaults to
            ``agents/llms/default.yaml``.
        poll_interval_seconds: how often to poll the event stream.
        agentid_audience: override AgentID audience claim; defaults to
            ``gateway_url``.
        identity: pre-loaded :class:`agent_id_client_sdk.Identity`. When
            ``None``, the SDK reads ``AGENTID_*`` env at runtime
            instead. Pass an Identity (e.g., from ``Identity.from_zip``)
            when orchestrating multiple agents in one process.
    """
    if gateway_url and dashboard_url:
        raise ValueError(
            "pass either gateway_url or dashboard_url, not both — "
            "dashboard_url derives the gateway URL from the trial_id"
        )
    if dashboard_url:
        dashboard_url = dashboard_url.rstrip("/")
        gateway_url = f"{dashboard_url}/api/trials/{trial_id}"
        # The JWT `aud` claim must match what the gateway-side verifier
        # expects, which is the dashboard's public origin — NOT the
        # trial-prefixed URL (the path is multiplexing, not identity).
        # Default audience accordingly when caller didn't override.
        if agentid_audience is None:
            agentid_audience = dashboard_url
    if not gateway_url:
        raise ValueError("gateway_url or dashboard_url is required (got neither)")
    gateway_url = gateway_url.rstrip("/")
    persona_path = persona_path or (_DEFAULT_PERSONA_DIR / f"{persona}.yaml")
    llm_config_path = llm_config_path or _DEFAULT_LLM_CONFIG_PATH

    if not persona_path.is_file():
        raise ValueError(f"Persona YAML not found: {persona_path}")
    if not llm_config_path.is_file():
        raise ValueError(f"LLM config YAML not found: {llm_config_path}")

    sys_prompt = _load_persona_prompt(persona_path)
    llm_config = _load_llm_config(llm_config_path, llm)

    return RunnerConfig(
        persona=persona,
        llm=llm,
        trial_id=trial_id,
        gateway_url=gateway_url,
        sys_prompt=sys_prompt,
        llm_config=llm_config,
        poll_interval_seconds=poll_interval_seconds,
        agentid_audience=agentid_audience,
        persona_path=persona_path,
        llm_config_path=llm_config_path,
        identity=identity,
    )


def load_config_from_env() -> RunnerConfig:
    """Build a :class:`RunnerConfig` from environment variables.

    Required:
        ``DOJOZERO_PERSONA``: persona name, e.g. ``degen``.
        ``DOJOZERO_LLM``: LLM display name matching ``model_display_name`` in
            the LLM matrix YAML, e.g. ``Claude``.
        ``DOJOZERO_TRIAL_ID``: trial to join.
        ``DOJOZERO_GATEWAY_URL``: gateway base URL.
        ``AGENTID_AGENT_ID``: AgentID identity (also requires
            ``AGENTID_AGENT_KID`` and ``AGENTID_PRIVATE_KEY``, both consumed
            directly by ``agent-id-client-sdk.Identity.from_env``).

    Optional:
        ``DOJOZERO_PERSONA_PATH``: override path to persona YAML; default is
            ``agents/personas/{persona}.yaml``.
        ``DOJOZERO_LLM_CONFIG_PATH``: override path to LLM matrix YAML;
            default is ``agents/llms/default.yaml``.
        ``DOJOZERO_POLL_INTERVAL_SECONDS``: default ``5``.
        ``DOJOZERO_AGENTID_AUDIENCE``: override AgentID audience claim; default is
            ``DOJOZERO_GATEWAY_URL``.

    Raises:
        ValueError: any required env var is missing or a path doesn't exist
            or the LLM display name isn't in the matrix.
    """
    persona = _require_env("DOJOZERO_PERSONA")
    llm = _require_env("DOJOZERO_LLM")
    trial_id = _require_env("DOJOZERO_TRIAL_ID")
    # Either DOJOZERO_DASHBOARD_URL (preferred — derives the trial-prefixed
    # gateway URL) or DOJOZERO_GATEWAY_URL (explicit, for standalone gateway).
    gateway_url = os.environ.get("DOJOZERO_GATEWAY_URL", "").strip() or None
    dashboard_url = os.environ.get("DOJOZERO_DASHBOARD_URL", "").strip() or None
    if not gateway_url and not dashboard_url:
        raise ValueError("DOJOZERO_DASHBOARD_URL or DOJOZERO_GATEWAY_URL is required")

    # Validate AgentID identity env is present. The SDK's
    # Identity.from_env() reads it again at runtime — we only check up
    # front so the failure mode is a clear config error rather than a
    # deep stack later.
    _require_env("AGENTID_AGENT_ID")
    _require_env("AGENTID_AGENT_KID")
    _require_env("AGENTID_PRIVATE_KEY")

    persona_path = (
        Path(os.environ["DOJOZERO_PERSONA_PATH"])
        if os.environ.get("DOJOZERO_PERSONA_PATH")
        else None
    )
    llm_config_path = (
        Path(os.environ["DOJOZERO_LLM_CONFIG_PATH"])
        if os.environ.get("DOJOZERO_LLM_CONFIG_PATH")
        else None
    )

    poll_interval_raw = os.environ.get("DOJOZERO_POLL_INTERVAL_SECONDS", "5")
    try:
        poll_interval_seconds = float(poll_interval_raw)
    except ValueError as exc:
        raise ValueError(
            f"DOJOZERO_POLL_INTERVAL_SECONDS must be a number, got: "
            f"{poll_interval_raw!r}"
        ) from exc

    agentid_audience = os.environ.get("DOJOZERO_AGENTID_AUDIENCE", "").strip() or None

    return build_config(
        persona=persona,
        llm=llm,
        trial_id=trial_id,
        gateway_url=gateway_url,
        dashboard_url=dashboard_url,
        persona_path=persona_path,
        llm_config_path=llm_config_path,
        poll_interval_seconds=poll_interval_seconds,
        agentid_audience=agentid_audience,
        identity=None,  # SDK reads AGENTID_* env at runtime
    )


def load_config_from_args(args: Any) -> RunnerConfig:
    """Build a :class:`RunnerConfig` from CLI args, falling back to env.

    Each arg is optional; when ``None`` we read the equivalent env var.
    The ``--portal-zip`` flag (when set) wraps ``Identity.from_zip``
    so callers can keep identity material packaged instead of unpacked
    into env vars. ``--portal-zip`` overrides any ``AGENTID_*`` env.
    """
    persona = args.persona or os.environ.get("DOJOZERO_PERSONA")
    llm = args.llm or os.environ.get("DOJOZERO_LLM")
    trial_id = args.trial_id or os.environ.get("DOJOZERO_TRIAL_ID")
    gateway_url = args.gateway_url or (
        os.environ.get("DOJOZERO_GATEWAY_URL", "").strip() or None
    )
    dashboard_url = args.dashboard_url or (
        os.environ.get("DOJOZERO_DASHBOARD_URL", "").strip() or None
    )

    if not persona:
        raise ValueError("persona is required (--persona or DOJOZERO_PERSONA)")
    if not llm:
        raise ValueError("llm is required (--llm or DOJOZERO_LLM)")
    if not trial_id:
        raise ValueError("trial_id is required (--trial-id or DOJOZERO_TRIAL_ID)")
    if not gateway_url and not dashboard_url:
        raise ValueError(
            "either --dashboard-url (preferred) or --gateway-url is required "
            "(env equivalents: DOJOZERO_DASHBOARD_URL, DOJOZERO_GATEWAY_URL)"
        )

    persona_path = (
        Path(args.persona_path)
        if args.persona_path
        else (
            Path(os.environ["DOJOZERO_PERSONA_PATH"])
            if os.environ.get("DOJOZERO_PERSONA_PATH")
            else None
        )
    )
    llm_config_path = (
        Path(args.llm_config_path)
        if args.llm_config_path
        else (
            Path(os.environ["DOJOZERO_LLM_CONFIG_PATH"])
            if os.environ.get("DOJOZERO_LLM_CONFIG_PATH")
            else None
        )
    )

    poll_interval_seconds = (
        args.poll_interval_seconds
        if args.poll_interval_seconds is not None
        else float(os.environ.get("DOJOZERO_POLL_INTERVAL_SECONDS", "5"))
    )
    agentid_audience = args.agentid_audience or (
        os.environ.get("DOJOZERO_AGENTID_AUDIENCE", "").strip() or None
    )

    portal_zip = args.portal_zip
    agent_profile = getattr(args, "agent_profile", None)
    if portal_zip and agent_profile:
        raise ValueError("pass --portal-zip OR --agent-profile, not both")

    identity = None
    if portal_zip:
        from agent_id_client_sdk import Identity  # noqa: PLC0415

        identity = Identity.from_zip(portal_zip)
    elif agent_profile:
        from agent_id_client_sdk import Identity  # noqa: PLC0415

        identity = Identity.from_profile(agent_profile)
    else:
        # No zip / profile → SDK will read AGENTID_* env at runtime.
        # Validate up front so the error is clear if neither is configured.
        if not os.environ.get("AGENTID_AGENT_ID"):
            raise ValueError(
                "no identity configured: pass --portal-zip <path>, "
                "--agent-profile <name>, or set AGENTID_AGENT_ID / "
                "AGENTID_AGENT_KID / AGENTID_PRIVATE_KEY"
            )

    return build_config(
        persona=persona,
        llm=llm,
        trial_id=trial_id,
        gateway_url=gateway_url,
        dashboard_url=dashboard_url,
        persona_path=persona_path,
        llm_config_path=llm_config_path,
        poll_interval_seconds=poll_interval_seconds,
        agentid_audience=agentid_audience,
        identity=identity,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required env var not set: {name}")
    return value


def _load_persona_prompt(path: Path) -> str:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    sys_prompt = data.get("sys_prompt")
    if not isinstance(sys_prompt, str) or not sys_prompt.strip():
        raise ValueError(
            f"Persona YAML at {path} is missing a non-empty 'sys_prompt' field"
        )
    return sys_prompt


def _load_llm_config(path: Path, llm_display_name: str) -> dict[str, Any]:
    """Load the LLM matrix and return the entry matching ``llm_display_name``.

    Match is case-insensitive against ``model_display_name`` to keep env
    config friendly (``DOJOZERO_LLM=claude`` vs the YAML's ``Claude``).
    """
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    entries = data.get("llm", [])
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"LLM config YAML at {path} is missing 'llm' list")

    target = llm_display_name.strip().casefold()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        display = str(entry.get("model_display_name", "")).strip().casefold()
        model_name = str(entry.get("model_name", "")).strip().casefold()
        if display == target or model_name == target:
            return entry

    available = ", ".join(
        sorted(
            str(e.get("model_display_name") or e.get("model_name") or "?")
            for e in entries
            if isinstance(e, dict)
        )
    )
    raise ValueError(
        f"LLM {llm_display_name!r} not found in {path}. Available: {available}"
    )
