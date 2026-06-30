"""Build a ModelScope AgentID client from stored credentials.

Opt-in: only used when a profile has an AgentID identity configured
(``dojozero-agent config --agentid-*``). Keeps the optional
``agent-id-client-sdk`` dependency out of the import path unless actually used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Keys persisted in the credentials file under ``profiles[<p>]["agentid"]``.
AGENTID_FIELDS = ("agent_id", "kid", "key_path", "idp_url", "audience")


def build_agentid_client(agentid: dict) -> tuple[Any, str]:
    """Build an ``agent_id_client_sdk.Client`` + audience from stored config.

    ``agentid`` must contain :data:`AGENTID_FIELDS`. Returns ``(client, audience)``.
    Raises ``RuntimeError`` with an install hint if the optional dependency is
    missing, or ``ValueError`` if a field is absent.
    """
    missing = [f for f in AGENTID_FIELDS if not agentid.get(f)]
    if missing:
        raise ValueError(f"AgentID config missing fields: {', '.join(missing)}")

    try:
        from agent_id_client_sdk import (
            Client,
            Identity,
        )
    except ImportError as exc:  # optional extra
        raise RuntimeError(
            "ModelScope AgentID auth needs the optional dependency. "
            "Install: pip install dojozero-client[agentid]"
        ) from exc

    key_path = Path(agentid["key_path"]).expanduser()
    identity = Identity(
        agent_id=agentid["agent_id"],
        kid=agentid["kid"],
        private_key_bytes=key_path.read_bytes(),
        idp_url=agentid["idp_url"],
    )
    audience = agentid["audience"]
    # ModelScope tokens carry no cnf.jkt — Bearer only, no DPoP.
    client = Client(identity, default_audience=audience, dpop=False)
    return client, audience
