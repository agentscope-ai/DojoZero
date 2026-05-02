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

    Built by :func:`load_config_from_env`. Holds everything needed to wire up
    the SDK transport, the LLM, and the persona prompt — no further env
    reads happen after this is constructed (except for AgentID env, which is
    read inside ``agent-id-client-sdk`` directly).
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
    gateway_url = _require_env("DOJOZERO_GATEWAY_URL").rstrip("/")

    # Validate AgentID identity is present. The SDK reads these env vars
    # itself in Identity.from_env() — we only check up front so the failure
    # mode is a clear config error rather than a deep stack later.
    _require_env("AGENTID_AGENT_ID")
    _require_env("AGENTID_AGENT_KID")
    _require_env("AGENTID_PRIVATE_KEY")

    persona_path = Path(
        os.environ.get(
            "DOJOZERO_PERSONA_PATH",
            str(_DEFAULT_PERSONA_DIR / f"{persona}.yaml"),
        )
    )
    llm_config_path = Path(
        os.environ.get("DOJOZERO_LLM_CONFIG_PATH", str(_DEFAULT_LLM_CONFIG_PATH))
    )

    if not persona_path.is_file():
        raise ValueError(f"Persona YAML not found: {persona_path}")
    if not llm_config_path.is_file():
        raise ValueError(f"LLM config YAML not found: {llm_config_path}")

    sys_prompt = _load_persona_prompt(persona_path)
    llm_config = _load_llm_config(llm_config_path, llm)

    poll_interval_raw = os.environ.get("DOJOZERO_POLL_INTERVAL_SECONDS", "5")
    try:
        poll_interval_seconds = float(poll_interval_raw)
    except ValueError as exc:
        raise ValueError(
            f"DOJOZERO_POLL_INTERVAL_SECONDS must be a number, got: "
            f"{poll_interval_raw!r}"
        ) from exc

    agentid_audience = os.environ.get("DOJOZERO_AGENTID_AUDIENCE", "").strip() or None

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
