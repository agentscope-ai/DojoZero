"""Runner configuration.

The runner takes one *agent profile* (identity + brain) plus trial
coordinates. Three ways to build a :class:`RunnerConfig`:

- :func:`load_config_from_env` — k8s pod / single-image style.
- :func:`load_config_from_args` — CLI flags, optionally with env
  fallback. Used by ``__main__.py``.
- :func:`build_config` — programmatic; both above reduce to it.

The brain (sys_prompt + model config) lives in ``agent.yaml`` inside an
agent profile dir (``~/.agentid/agents/<name>/agent.yaml``). CLI-level
overrides ``--sys-prompt-file`` / ``--model-config`` shadow the
profile's brain, useful for A/B testing or smoke-testing without
re-issuing a profile.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RunnerConfig:
    """Resolved runner configuration."""

    trial_id: str
    gateway_url: str
    sys_prompt: str
    llm_config: dict[str, Any]
    poll_interval_seconds: float
    agentid_audience: str | None  # None → defaults to ``gateway_url``
    # Display name for the ReActAgent, e.g. ``cli-agent`` or ``degen-claude``.
    # Cosmetic only — used in logs and the agent's name field.
    agent_display_name: str = "agent"
    # Pre-loaded AgentID identity. When None, the runner falls back to
    # ``Identity.from_env()`` at start time (k8s pod default).
    identity: Any = None
    # Approval mode: register such that every bet is gated by an
    # IdP-mediated approval flow (spec §7.6.7). When True, the runner
    # also registers the ``check_pending_bet`` tool so the agent can
    # poll for the principal's decision.
    request_approval: bool = False


def build_config(
    *,
    trial_id: str,
    sys_prompt: str,
    llm_config: dict[str, Any],
    gateway_url: str | None = None,
    dashboard_url: str | None = None,
    poll_interval_seconds: float = 5.0,
    agentid_audience: str | None = None,
    agent_display_name: str = "agent",
    identity: Any = None,
    request_approval: bool = False,
) -> RunnerConfig:
    """Build a :class:`RunnerConfig` from explicit values.

    Args:
        trial_id: trial to join.
        sys_prompt: persona / system prompt for the ReActAgent.
        llm_config: dict consumed by ``_llm.create_model``. Required keys
            depend on ``model_type`` but typically include ``model_type``,
            ``model_name``, and either ``api_key`` or ``api_key_env``.
        gateway_url: trial gateway base URL. Use this for standalone
            ``dojo0 serve --trial-id ...`` (gateway at root). Mutually
            exclusive with ``dashboard_url``.
        dashboard_url: dashboard server base URL. The trial's gateway is
            derived as ``{dashboard_url}/api/trials/{trial_id}``.
            Mutually exclusive with ``gateway_url``.
        poll_interval_seconds: event poll cadence.
        agentid_audience: override AgentID JWT ``aud`` claim. When
            ``None`` and ``dashboard_url`` is set, defaults to the
            dashboard origin (matching what the gateway-side verifier
            expects). When ``None`` and only ``gateway_url`` is set,
            defaults to ``gateway_url``.
        agent_display_name: cosmetic name for the ReActAgent.
        identity: pre-loaded ``agent_id_client_sdk.Identity``. ``None``
            means the SDK reads ``AGENTID_*`` env at runtime instead.
    """
    if gateway_url and dashboard_url:
        raise ValueError(
            "pass either gateway_url or dashboard_url, not both — "
            "dashboard_url derives the gateway URL from the trial_id"
        )
    if dashboard_url:
        dashboard_url = dashboard_url.rstrip("/")
        gateway_url = f"{dashboard_url}/api/trials/{trial_id}"
        if agentid_audience is None:
            # JWT `aud` must match what the gateway-side verifier expects,
            # which is the dashboard's public origin — NOT the trial-prefixed
            # URL (the path is multiplexing, not identity).
            agentid_audience = dashboard_url
    if not gateway_url:
        raise ValueError("gateway_url or dashboard_url is required (got neither)")
    gateway_url = gateway_url.rstrip("/")

    if not sys_prompt or not sys_prompt.strip():
        raise ValueError("sys_prompt is required and must be non-empty")
    if not isinstance(llm_config, dict) or not llm_config:
        raise ValueError("llm_config is required and must be a non-empty dict")

    return RunnerConfig(
        trial_id=trial_id,
        gateway_url=gateway_url,
        sys_prompt=sys_prompt,
        llm_config=llm_config,
        poll_interval_seconds=poll_interval_seconds,
        agentid_audience=agentid_audience,
        agent_display_name=agent_display_name,
        identity=identity,
        request_approval=request_approval,
    )


def load_config_from_env() -> RunnerConfig:
    """Build a :class:`RunnerConfig` from environment variables.

    Required:
        ``DOJOZERO_TRIAL_ID``: trial to join.
        ``DOJOZERO_DASHBOARD_URL`` *or* ``DOJOZERO_GATEWAY_URL``: where
            the gateway lives.
        Identity: ``AGENTID_*`` env (read by ``Identity.from_env``) *or*
            ``DOJOZERO_AGENT_PROFILE`` pointing to a profile dir.

    Brain (one of the following must resolve to a sys_prompt + model):
        - ``DOJOZERO_AGENT_BRAIN``: path to a YAML with both
          ``sys_prompt`` and ``model:``. The canonical shape produced
          by ``dojo0 agents build-brains``.
        - ``DOJOZERO_AGENT_PROMPT`` *and* ``DOJOZERO_AGENT_MODEL``:
          paths to files containing the prompt (raw text) and the model
          config (YAML). Two-file split, useful for long markdown prompts.

    Optional:
        ``DOJOZERO_POLL_INTERVAL_SECONDS``: default ``5``.
        ``DOJOZERO_AGENTID_AUDIENCE``: override JWT ``aud``.

    Raises:
        ValueError: any required input is missing or malformed.
    """
    trial_id = _require_env("DOJOZERO_TRIAL_ID")

    gateway_url = os.environ.get("DOJOZERO_GATEWAY_URL", "").strip() or None
    dashboard_url = os.environ.get("DOJOZERO_DASHBOARD_URL", "").strip() or None
    if not gateway_url and not dashboard_url:
        raise ValueError("DOJOZERO_DASHBOARD_URL or DOJOZERO_GATEWAY_URL is required")

    agent_profile = os.environ.get("DOJOZERO_AGENT_PROFILE", "").strip() or None
    agent_brain_path = os.environ.get("DOJOZERO_AGENT_BRAIN", "").strip() or None
    agent_prompt_path = os.environ.get("DOJOZERO_AGENT_PROMPT", "").strip() or None
    agent_model_path = os.environ.get("DOJOZERO_AGENT_MODEL", "").strip() or None

    sys_prompt, llm_config, agent_display_name = _resolve_brain(
        agent_brain_path=agent_brain_path,
        agent_prompt_path=agent_prompt_path,
        agent_model_path=agent_model_path,
        agent_profile=agent_profile,
    )

    # Identity: profile takes precedence over env (identity-only;
    # the brain is decoupled from the profile dir).
    identity: Any = None
    if agent_profile:
        from agent_id_client_sdk import Identity  # noqa: PLC0415

        identity = Identity.from_profile(agent_profile)
    else:
        # SDK will Identity.from_env() at runtime. Validate up front.
        for var in ("AGENTID_AGENT_ID", "AGENTID_AGENT_KID", "AGENTID_PRIVATE_KEY"):
            _require_env(var)

    poll_interval_raw = os.environ.get("DOJOZERO_POLL_INTERVAL_SECONDS", "5")
    try:
        poll_interval_seconds = float(poll_interval_raw)
    except ValueError as exc:
        raise ValueError(
            f"DOJOZERO_POLL_INTERVAL_SECONDS must be a number, got: "
            f"{poll_interval_raw!r}"
        ) from exc

    agentid_audience = os.environ.get("DOJOZERO_AGENTID_AUDIENCE", "").strip() or None
    request_approval = os.environ.get(
        "DOJOZERO_REQUEST_APPROVAL", ""
    ).strip().lower() in ("1", "true", "yes")

    return build_config(
        trial_id=trial_id,
        gateway_url=gateway_url,
        dashboard_url=dashboard_url,
        sys_prompt=sys_prompt,
        llm_config=llm_config,
        poll_interval_seconds=poll_interval_seconds,
        agentid_audience=agentid_audience,
        agent_display_name=agent_display_name,
        identity=identity,
        request_approval=request_approval,
    )


def load_config_from_args(args: Any) -> RunnerConfig:
    """Build a :class:`RunnerConfig` from CLI args, falling back to env.

    Identity sources (mutually exclusive):
      - ``--agent-profile``: identity from an agent profile dir
      - ``--agent-zip``: identity from a zip (e.g. portal export)
      - none: ``Identity.from_env()`` at runtime (k8s pod fallback)

    Brain sources (decoupled from --agent-profile, which is identity-only):
      - ``--agent-brain <path>``: single YAML with sys_prompt + model.
      - ``--sys-prompt-file`` and/or ``--model-config``: piecewise
        overrides. Win over ``--agent-brain`` when both are given.
      - Env equivalents: ``DOJOZERO_AGENT_BRAIN``,
        ``DOJOZERO_SYS_PROMPT_FILE``, ``DOJOZERO_MODEL_CONFIG``.
    """
    trial_id = args.trial_id or os.environ.get("DOJOZERO_TRIAL_ID")
    gateway_url = args.gateway_url or (
        os.environ.get("DOJOZERO_GATEWAY_URL", "").strip() or None
    )
    dashboard_url = args.dashboard_url or (
        os.environ.get("DOJOZERO_DASHBOARD_URL", "").strip() or None
    )

    if not trial_id:
        raise ValueError("trial_id is required (--trial-id or DOJOZERO_TRIAL_ID)")
    if not gateway_url and not dashboard_url:
        raise ValueError(
            "either --dashboard-url (preferred) or --gateway-url is required "
            "(env equivalents: DOJOZERO_DASHBOARD_URL, DOJOZERO_GATEWAY_URL)"
        )

    agent_zip = getattr(args, "agent_zip", None) or (
        os.environ.get("DOJOZERO_AGENT_ZIP", "").strip() or None
    )
    agent_profile = getattr(args, "agent_profile", None) or (
        os.environ.get("DOJOZERO_AGENT_PROFILE", "").strip() or None
    )

    if agent_zip and agent_profile:
        raise ValueError("pass at most one of: --agent-zip, --agent-profile")

    identity = _load_identity(
        agent_zip=agent_zip,
        agent_profile=agent_profile,
    )

    agent_brain_path = getattr(args, "agent_brain", None) or (
        os.environ.get("DOJOZERO_AGENT_BRAIN", "").strip() or None
    )
    agent_prompt_path = getattr(args, "agent_prompt", None) or (
        os.environ.get("DOJOZERO_AGENT_PROMPT", "").strip() or None
    )
    agent_model_path = getattr(args, "agent_model", None) or (
        os.environ.get("DOJOZERO_AGENT_MODEL", "").strip() or None
    )

    sys_prompt, llm_config, agent_display_name = _resolve_brain(
        agent_brain_path=agent_brain_path,
        agent_prompt_path=agent_prompt_path,
        agent_model_path=agent_model_path,
        agent_profile=agent_profile,
    )

    poll_interval_seconds = (
        args.poll_interval_seconds
        if args.poll_interval_seconds is not None
        else float(os.environ.get("DOJOZERO_POLL_INTERVAL_SECONDS", "5"))
    )
    agentid_audience = args.agentid_audience or (
        os.environ.get("DOJOZERO_AGENTID_AUDIENCE", "").strip() or None
    )
    request_approval = bool(getattr(args, "request_approval", False)) or (
        os.environ.get("DOJOZERO_REQUEST_APPROVAL", "").strip().lower()
        in ("1", "true", "yes")
    )

    return build_config(
        trial_id=trial_id,
        gateway_url=gateway_url,
        dashboard_url=dashboard_url,
        sys_prompt=sys_prompt,
        llm_config=llm_config,
        poll_interval_seconds=poll_interval_seconds,
        agentid_audience=agentid_audience,
        agent_display_name=agent_display_name,
        identity=identity,
        request_approval=request_approval,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required env var not set: {name}")
    return value


def _resolve_brain(
    *,
    agent_brain_path: str | None,
    agent_prompt_path: str | None,
    agent_model_path: str | None,
    agent_profile: str | None,
) -> tuple[str, dict[str, Any], str]:
    """Resolve sys_prompt + llm_config + display_name from the inputs.

    Brain comes from one of:

    - ``--agent-brain <path>``: a single YAML with both ``sys_prompt``
      and ``model:`` (the canonical shape; what ``dojo0 agents
      build-brains`` produces).
    - ``--agent-prompt`` and ``--agent-model``: two-file split, useful
      when the prompt is a long markdown doc and the model is a tiny YAML.

    Combination is allowed: if both ``--agent-brain`` and the
    piecewise flags are set, the piecewise flags override individual
    fields. Pure piecewise without ``--agent-brain`` is fine too.

    Display name is derived from the agent_brain filename, else the
    agent_prompt filename, else the profile name, else a generic ``agent``.
    The agent profile contributes only the display name (and identity,
    handled elsewhere) — it never sources the brain itself.
    """
    config_sys_prompt: str | None = None
    config_model: dict[str, Any] | None = None

    if agent_brain_path:
        from agent_id_client_sdk import AgentConfig  # noqa: PLC0415

        cfg = AgentConfig.from_yaml_file(agent_brain_path)
        config_sys_prompt = cfg.sys_prompt
        config_model = cfg.model

    sys_prompt: str | None = None
    if agent_prompt_path:
        prompt_path = Path(agent_prompt_path)
        if not prompt_path.is_file():
            raise ValueError(f"agent prompt file not found: {prompt_path}")
        sys_prompt = prompt_path.read_text(encoding="utf-8")
    elif config_sys_prompt is not None:
        sys_prompt = config_sys_prompt

    if not sys_prompt or not sys_prompt.strip():
        raise ValueError(
            "no sys_prompt resolved: pass --agent-brain, --agent-prompt, "
            "or set DOJOZERO_AGENT_BRAIN / DOJOZERO_AGENT_PROMPT"
        )

    llm_config: dict[str, Any] | None = None
    if agent_model_path:
        path = Path(agent_model_path)
        if not path.is_file():
            raise ValueError(f"agent model file not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict) or not data:
            raise ValueError(
                f"agent model file at {path} must be a non-empty YAML mapping"
            )
        llm_config = _normalize_model_config(data)
    elif config_model is not None:
        llm_config = _normalize_model_config(config_model)

    if not llm_config:
        raise ValueError(
            "no model config resolved: pass --agent-brain, --agent-model, "
            "or set DOJOZERO_AGENT_BRAIN / DOJOZERO_AGENT_MODEL"
        )

    display_name = (
        Path(agent_brain_path).stem
        if agent_brain_path
        else (
            Path(agent_prompt_path).stem
            if agent_prompt_path
            else (Path(agent_profile).name if agent_profile else "agent")
        )
    )

    return sys_prompt, llm_config, display_name


def _normalize_model_config(model: dict[str, Any]) -> dict[str, Any]:
    """Normalize the agent-brain ``model:`` block to the runner's llm_config shape.

    The agent-brain schema uses ``type``/``name``; the runner's
    ``create_model`` expects ``model_type``/``model_name``. Translate so
    we don't churn the LLM glue layer.
    """
    out = dict(model)
    if "type" in out and "model_type" not in out:
        out["model_type"] = out.pop("type")
    if "name" in out and "model_name" not in out:
        out["model_name"] = out.pop("name")
    return out


def _load_identity(
    *,
    agent_zip: str | None,
    agent_profile: str | None,
) -> Any:
    """Load an ``Identity`` from the configured source. Returns ``None`` to
    defer loading to the SDK's ``Identity.from_env()`` at runtime.
    """
    from agent_id_client_sdk import Identity  # noqa: PLC0415

    if agent_zip:
        return Identity.from_zip(agent_zip)
    if agent_profile:
        return Identity.from_profile(agent_profile)
    # Fall through: SDK reads AGENTID_* env at runtime.
    if not os.environ.get("AGENTID_AGENT_ID"):
        raise ValueError(
            "no identity configured: pass --agent-profile, --agent-zip, or "
            "set AGENTID_AGENT_ID / AGENTID_AGENT_KID / AGENTID_PRIVATE_KEY"
        )
    return None
