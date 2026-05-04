"""Activity event emission helpers for the AgentID provider.

Four Tier-1 categories that DojoZero emits as a first adopter of
``agent-activity``:

- ``auth.deny`` — failed Bearer-token verifications. Catches forged /
  expired / wrong-audience attempts that ``auth.verify`` doesn't see.
- ``session.start`` — agent successfully registers for a trial.
- ``session.end`` — agent unregisters or the trial closes its session.
- ``transfer.value`` — agent's broker accepted a bet. The product event:
  what the agent actually did with its money. Pairs naturally with a
  later ``dojozero.bet.settled`` (Tier-2 namespaced) when a follow-up
  commit wires settlement.

Per-request ``auth.verify`` is intentionally **not** auto-emitted — the
SDK's auto-emit fires on every Bearer-authenticated request, which would
flood the activity service with low-information duplicates (~2.7M
events/day at full DojoZero load). Configure
``report_auto_verify=False`` on the verifier and rely on
``session.start`` to mark the per-session "this principal proved
identity" moment.

All emitters are **best-effort**: any failure here logs at WARNING and
returns. The activity service is observability infrastructure; if it's
down or misconfigured the gateway must keep authenticating and serving
requests.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import jwt

if TYPE_CHECKING:
    from agent_id_service_sdk import Verifier
    from agent_id_service_sdk import VerifiedAgent as _VerifiedAgent

logger = logging.getLogger(__name__)


def _build_verified_agent(
    *,
    agent_id: str,
    agent_name: str = "",
    principal: dict[str, Any] | None = None,
    issuer: str = "",
    kid: str = "",
    expires_at: datetime | None = None,
    raw_claims: dict[str, Any] | None = None,
) -> "_VerifiedAgent":
    """Construct a ``VerifiedAgent`` for the activity emission path.

    The SDK's ``report_event`` reads a fixed set of fields; everything
    else can be empty defaults. We fill in ``raw_claims["_kid"]`` because
    that's where ``report_event`` looks for the key id used to sign the
    original token.
    """
    from agent_id_service_sdk import VerifiedAgent  # noqa: PLC0415

    claims = dict(raw_claims or {})
    if kid and "_kid" not in claims:
        claims["_kid"] = kid
    return VerifiedAgent(
        agent_id=agent_id,
        agent_name=agent_name,
        principal=principal or {},
        capabilities=[],
        scopes={},
        delegation=None,
        model_info=None,
        issuer=issuer,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=1),
        raw_claims=claims,
    )


async def emit_auth_deny(
    verifier: "Verifier | None",
    *,
    token: str,
    route: str,
    reason: str,
) -> None:
    """Emit ``auth.deny`` for a Bearer token that failed verification.

    Pulls claimed identity from the unverified JWT payload so the
    activity service can record "agent X *claimed* to act but failed
    crypto verification" — a useful security signal for correlated
    attempts. Falls through quietly if ``verifier`` is ``None``, the
    activity reporter is unconfigured, or the token is so malformed we
    can't even parse the unverified header.
    """
    if verifier is None:
        return

    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        unverified_header = jwt.get_unverified_header(token)
    except Exception:  # noqa: BLE001 — token may be garbage
        # Best-effort: emit with empty identity so the activity service
        # at least sees the deny attempt.
        unverified = {}
        unverified_header = {}

    agent = _build_verified_agent(
        agent_id=str(unverified.get("sub") or "unknown"),
        agent_name=str(unverified.get("agent_name") or ""),
        principal=(
            unverified.get("principal")
            if isinstance(unverified.get("principal"), dict)
            else {}
        )
        or {},
        issuer=str(unverified.get("iss") or ""),
        kid=str(unverified_header.get("kid") or ""),
        raw_claims=unverified if isinstance(unverified, dict) else {},
    )

    try:
        await verifier.report_event(
            category="auth.deny",
            agent=agent,
            outcome="failure",
            route=route,
            payload={"route": route, "reason": reason},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_auth_deny: emission failed: %s", exc)


async def emit_session_start(
    verifier: "Verifier | None",
    *,
    agent_id: str,
    trial_id: str,
    agent_name: str = "",
    persona: str | None = None,
    model: str | None = None,
    sport_type: str | None = None,
) -> None:
    """Emit ``session.start`` after a successful agent registration."""
    if verifier is None:
        return

    payload_extras: dict[str, Any] = {"trial_id": trial_id}
    if persona:
        payload_extras["persona"] = persona
    if model:
        payload_extras["model"] = model
    if sport_type:
        payload_extras["sport_type"] = sport_type

    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_session_start(
            agent,
            session_id=trial_id,
            **payload_extras,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_session_start: emission failed: %s", exc)


async def emit_session_end(
    verifier: "Verifier | None",
    *,
    agent_id: str,
    trial_id: str,
    duration_ms: int,
    agent_name: str = "",
    final_balance: str | None = None,
    last_observed_sequence: int | None = None,
    bet_count: int | None = None,
) -> None:
    """Emit ``session.end`` when an agent unregisters or the trial closes."""
    if verifier is None:
        return

    payload_extras: dict[str, Any] = {"trial_id": trial_id}
    if final_balance is not None:
        payload_extras["final_balance"] = final_balance
    if last_observed_sequence is not None:
        payload_extras["last_observed_sequence"] = last_observed_sequence
    if bet_count is not None:
        payload_extras["bet_count"] = bet_count

    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_session_end(
            agent,
            session_id=trial_id,
            duration_ms=max(0, duration_ms),
            **payload_extras,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_session_end: emission failed: %s", exc)


async def emit_transfer_value(
    verifier: "Verifier | None",
    *,
    agent_id: str,
    trial_id: str,
    amount: str,
    market: str,
    selection: str,
    bet_id: str,
    event_id: str = "",
    probability: str | None = None,
    shares: str | None = None,
    reference_sequence: int | None = None,
    agent_name: str = "",
) -> None:
    """Emit ``transfer.value`` for an accepted bet.

    AIP convention: ``transfer.value`` records *outgoing* value movement —
    the stake the agent committed. Bet settlement (win / loss / payout)
    is a separate event that lands in a follow-up commit. ``amount`` is
    the stake size as a decimal string; the activity service preserves
    string formatting to avoid float rounding across hubs.

    The payload is rich on purpose — every field is useful for
    cross-platform analytics (which markets does each persona prefer?
    how big are typical stakes? does this LLM size bets sensibly given
    its bankroll?). DojoZero is the AIP team's first product-domain
    adopter and this is the most product-meaningful event we emit.
    """
    if verifier is None:
        return

    payload: dict[str, Any] = {
        "trial_id": trial_id,
        "amount": amount,
        "market": market,
        "selection": selection,
        "bet_id": bet_id,
    }
    if event_id:
        payload["event_id"] = event_id
    if probability is not None:
        payload["probability"] = probability
    if shares is not None:
        payload["shares"] = shares
    if reference_sequence is not None:
        payload["reference_sequence"] = reference_sequence

    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_event(
            category="transfer.value",
            agent=agent,
            payload=payload,
            session_id=trial_id,
            outcome="success",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_transfer_value: emission failed: %s", exc)


__all__ = [
    "emit_auth_deny",
    "emit_session_end",
    "emit_session_start",
    "emit_transfer_value",
]
