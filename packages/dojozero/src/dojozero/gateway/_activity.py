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

import hashlib
import json
import logging
import uuid
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
    error_class: str,
) -> None:
    """Emit ``auth.deny`` for a Bearer token that failed verification.

    Payload is the canonical Tier-1 ``{route, error_class}`` shape. The
    ``error_class`` is a short code naming the failure mode
    (``token_expired``, ``token_invalid``, ``signature_invalid``,
    ``provider_untrusted``). Cross-hub anomaly detection consumers
    aggregate by this field.

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
            payload={"route": route, "error_class": error_class},
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
    """Emit ``session.start`` after a successful agent registration.

    Canonical Tier-1 payload is ``{session_id, scenario?, attributes?}``.
    DojoZero-specific context (persona, model, sport_type) goes into the
    open ``attributes`` dict so it doesn't pollute the cross-hub
    canonical schema. Aggregation that wants persona-level analytics
    subscribes to a future Tier-2 ``dojozero.session_attributes`` event.
    """
    if verifier is None:
        return

    attributes: dict[str, Any] = {}
    if persona:
        attributes["persona"] = persona
    if model:
        attributes["model"] = model
    if sport_type:
        attributes["sport_type"] = sport_type

    extras: dict[str, Any] = {"scenario": "trial"}
    if attributes:
        extras["attributes"] = attributes

    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_session_start(
            agent,
            session_id=trial_id,
            **extras,
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
    outcome: str = "completed",
    final_balance: str | None = None,
    last_observed_sequence: int | None = None,
    bet_count: int | None = None,
) -> None:
    """Emit ``session.end`` when an agent unregisters or the trial closes.

    Canonical Tier-1 payload is
    ``{session_id, duration_ms, outcome?, total_tokens?, total_cost_usd?, summary?}``.
    DojoZero-specific roll-ups (final_balance, last_observed_sequence,
    bet_count) go into the open ``summary`` dict. ``total_tokens`` and
    ``total_cost_usd`` aren't tracked at the gateway level today; they'd
    come from the runner if we wired LLM-cost rollup later.
    """
    if verifier is None:
        return

    summary: dict[str, Any] = {}
    if final_balance is not None:
        summary["final_balance"] = final_balance
    if last_observed_sequence is not None:
        summary["last_observed_sequence"] = last_observed_sequence
    if bet_count is not None:
        summary["bet_count"] = bet_count

    extras: dict[str, Any] = {"outcome": outcome}
    if summary:
        extras["summary"] = summary

    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_session_end(
            agent,
            session_id=trial_id,
            duration_ms=max(0, duration_ms),
            **extras,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_session_end: emission failed: %s", exc)


def _amount_bucket(amount: str | float) -> str:
    """Bucket a raw amount into the canonical Tier-1 bracket.

    Mirrors ``aip-activity/app/schemas/categories.py:AMOUNT_BUCKETS``. The
    bucket whitelist is fixed at the activity service; if it changes,
    this function and the server enum need to migrate together. Negative
    or unparseable amounts fall into ``lt_100`` defensively.
    """
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return "lt_100"
    if v < 100:
        return "lt_100"
    if v < 1_000:
        return "100_1k"
    if v < 10_000:
        return "1k_10k"
    return "10k_plus"


async def emit_model_call(
    verifier: "Verifier | None",
    *,
    agent_id: str,
    trial_id: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int | None = None,
    cost_usd: float | None = None,
    purpose: str | None = None,
    outcome: str | None = None,
    linked_tool_invocation_id: str | None = None,
    agent_name: str = "",
) -> None:
    """Emit ``model.call`` for an LLM invocation.

    Canonical Tier-1 payload:
    ``{model, tokens_in, tokens_out, latency_ms?, cost_usd?, purpose?,
       outcome?, linked_tool_invocation_id?}``.

    The runner reports model usage *to the gateway* (not directly to
    aip-activity) so the hub stays the single source of activity emission.
    The gateway then forwards via the same verifier path used by the
    other Tier-1 emitters here. Best-effort: failures log + return.
    """
    if verifier is None:
        return

    payload: dict[str, Any] = {
        "model": model,
        "tokens_in": max(0, int(tokens_in)),
        "tokens_out": max(0, int(tokens_out)),
    }
    if latency_ms is not None:
        payload["latency_ms"] = max(0, int(latency_ms))
    if cost_usd is not None:
        payload["cost_usd"] = max(0.0, float(cost_usd))
    if purpose is not None:
        payload["purpose"] = purpose
    if outcome is not None:
        payload["outcome"] = outcome
    if linked_tool_invocation_id is not None:
        payload["linked_tool_invocation_id"] = linked_tool_invocation_id

    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_event(
            category="model.call",
            agent=agent,
            payload=payload,
            session_id=trial_id,
            outcome=outcome or "success",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_model_call: emission failed: %s", exc)


def _hash_args(args: dict[str, Any] | None) -> str:
    """Hash an args dict deterministically as ``sha256:<hex>``.

    Canonical-JSON form (sorted keys, no whitespace) so the same args
    always hash the same. Empty/None hashes the empty object — a stable
    sentinel "we ran but with no args worth recording".
    """
    payload = args or {}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def new_tool_invocation_id() -> str:
    """Mint a fresh ``tool_invocation_id`` (callers may also pass their own)."""
    return f"inv_{uuid.uuid4().hex}"


async def emit_tool_use(
    verifier: "Verifier | None",
    *,
    agent_id: str,
    trial_id: str,
    tool_name: str,
    args: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    success: bool = True,
    linked_transfer_id: str | None = None,
    tool_invocation_id: str | None = None,
    agent_name: str = "",
) -> str:
    """Emit ``tool.use`` for an agent's tool invocation.

    Canonical Tier-1 payload:
    ``{tool_name, args_hash, tool_invocation_id, duration_ms?, success?,
       linked_transfer_id?}``.

    Returns the ``tool_invocation_id`` so callers that emit other events
    in the same invocation (e.g., a paired ``transfer.value`` where the
    ``transaction_id`` ends up in ``linked_transfer_id`` here) can join.

    ``args_hash`` is sha256 of the canonical-JSON form of ``args`` so raw
    arguments never leave the gateway. Hub-specific richness (which
    market, which selection) belongs in a Tier-2 ``dojozero.bet_decision``
    event linked via the same ``transaction_id``.
    """
    invocation_id = tool_invocation_id or new_tool_invocation_id()
    if verifier is None:
        return invocation_id

    payload: dict[str, Any] = {
        "tool_name": tool_name,
        "args_hash": _hash_args(args),
        "tool_invocation_id": invocation_id,
    }
    if duration_ms is not None:
        payload["duration_ms"] = max(0, duration_ms)
    payload["success"] = bool(success)
    if linked_transfer_id is not None:
        payload["linked_transfer_id"] = linked_transfer_id

    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_event(
            category="tool.use",
            agent=agent,
            payload=payload,
            session_id=trial_id,
            outcome="success" if success else "failure",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_tool_use: emission failed: %s", exc)
    return invocation_id


async def emit_transfer_value(
    verifier: "Verifier | None",
    *,
    agent_id: str,
    trial_id: str,
    amount: str,
    bet_id: str,
    currency: str = "USD",
    direction: str = "out",
    purpose: str = "stake",
    counterparty_principal_id: str | None = None,
    linked_tier2: str | None = "dojozero.bet_decision",
    agent_name: str = "",
) -> None:
    """Emit ``transfer.value`` for an accepted bet.

    Canonical Tier-1 payload:
    ``{amount_bucket, currency, direction, amount?, purpose, transaction_id,
       counterparty_principal_id?, linked_tier2?}``.

    Hub-specific richness (market, selection, probability, shares, etc.)
    does NOT go here — it belongs in the linked Tier-2
    ``dojozero.bet_decision`` event (emitted alongside this one once the
    Tier-2 schema lands). ``transaction_id`` (= ``bet_id``) is the join
    key.

    DojoZero defaults: stakes are *outgoing* value to the trial broker,
    purpose ``stake``, currency ``USD`` (informal — DojoZero trials use
    USD-denominated paper money). ``linked_tier2`` defaults to
    ``dojozero.bet_decision`` as a forward reference; consumers who join
    by ``transaction_id`` will find the Tier-2 event when it ships.
    """
    if verifier is None:
        return

    payload: dict[str, Any] = {
        "amount_bucket": _amount_bucket(amount),
        "currency": currency,
        "direction": direction,
        # Raw amount kept at privacy_level=full; activity service drops it
        # at summary. amount_bucket is what cross-hub aggregation reads.
        "amount": float(amount) if amount else 0.0,
        "purpose": purpose,
        "transaction_id": bet_id,
    }
    if counterparty_principal_id is not None:
        payload["counterparty_principal_id"] = counterparty_principal_id
    if linked_tier2 is not None:
        payload["linked_tier2"] = linked_tier2

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


def _confidence_bucket(confidence: float | None) -> str | None:
    """Bucket a 0..1 confidence into the Tier-2 enum (low/medium/high)."""
    if confidence is None:
        return None
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return None
    if c < 0.4:
        return "low"
    if c < 0.7:
        return "medium"
    return "high"


def _stake_bucket(amount: str | float) -> str:
    """Stake bucketing for the dojozero.bet_decision Tier-2 schema.

    Same buckets as the Tier-1 ``transfer.value`` ``amount_bucket`` so
    consumers can reconcile across the linked events.
    """
    return _amount_bucket(amount)


# Map BetRequest.market → dojozero.bet_decision.decision_kind enum.
_DECISION_KIND_BY_MARKET = {
    "moneyline": "moneyline",
    "spread": "spread",
    "total": "over_under",
}


async def emit_bet_decision(
    verifier: "Verifier | None",
    *,
    agent_id: str,
    trial_id: str,
    bet_id: str,
    market: str,
    selection: str,
    amount: str,
    model: str | None = None,
    confidence: float | None = None,
    rationale: str | None = None,
    sport: str | None = None,
    game_id: str | None = None,
    agent_name: str = "",
) -> None:
    """Emit ``dojozero.bet_decision`` (Tier-2) joined to ``transfer.value``
    via ``transaction_id`` (= ``bet_id``).

    The Tier-2 schema is published at
    ``<service_id>/.well-known/agent-id-activity-schemas/bet_decision/1.0.0``;
    aip-activity validates payloads against it.

    ``rationale`` is hashed server-side (sha256) — raw natural-language
    reasoning never leaves the gateway. ``confidence`` is bucketed
    (low/medium/high) so the cardinality stays useful for cross-trial
    aggregation.
    """
    if verifier is None:
        return

    decision_kind = _DECISION_KIND_BY_MARKET.get(market.lower())
    if decision_kind is None:
        # Unrecognised market — refuse to emit a malformed Tier-2 event
        # rather than risk schema validation failure at the activity service.
        logger.warning(
            "emit_bet_decision: unknown market %r; skipping Tier-2 emission", market
        )
        return

    payload: dict[str, Any] = {
        "transaction_id": bet_id,
        "decision_kind": decision_kind,
        "selection": selection,
        "stake_bucket": _stake_bucket(amount),
    }
    bucket = _confidence_bucket(confidence)
    if bucket is not None:
        payload["confidence_bucket"] = bucket
    if rationale:
        payload["rationale_hash"] = (
            "sha256:" + hashlib.sha256(rationale.encode("utf-8")).hexdigest()
        )
    if model:
        payload["model"] = model
    if game_id:
        payload["game_id"] = game_id
    if sport:
        payload["sport"] = sport

    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_event(
            category="dojozero.bet_decision",
            agent=agent,
            payload=payload,
            session_id=trial_id,
            outcome="success",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_bet_decision: emission failed: %s", exc)


async def emit_approval_requested(
    verifier: "Verifier | None",
    *,
    agent_id: str,
    trial_id: str,
    approval_id: str,
    resource: str,
    agent_name: str = "",
) -> None:
    """Emit ``approval.requested`` when the hub has submitted an approval to its IdP.

    Canonical Tier-1 payload:
    ``{approval_id, resource, idp_status: "pending"}``.

    Resource is what the agent was trying to do (e.g.
    ``dojozero.place_bet``). Activity consumers join on
    ``approval_id`` to reconstruct the full request → grant → action
    chain.
    """
    if verifier is None:
        return
    payload = {
        "approval_id": approval_id,
        "resource": resource,
        "idp_status": "pending",
    }
    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_event(
            category="approval.requested",
            agent=agent,
            payload=payload,
            session_id=trial_id,
            outcome="success",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_approval_requested: emission failed: %s", exc)


async def emit_approval_granted(
    verifier: "Verifier | None",
    *,
    agent_id: str,
    trial_id: str,
    approval_id: str,
    decided_by: str | None,
    resource: str,
    agent_name: str = "",
) -> None:
    """Emit ``approval.granted`` when a principal approves and the hub minted a grant.

    Canonical Tier-1 payload:
    ``{approval_id, resource, decided_by?}``.

    Linked to a prior ``approval.requested`` by ``approval_id`` and
    typically followed by a ``transfer.value`` (or whatever action the
    grant authorized) on the same agent in the same session.
    """
    if verifier is None:
        return
    payload: dict[str, Any] = {
        "approval_id": approval_id,
        "resource": resource,
    }
    if decided_by is not None:
        payload["decided_by"] = decided_by
    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_event(
            category="approval.granted",
            agent=agent,
            payload=payload,
            session_id=trial_id,
            outcome="success",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_approval_granted: emission failed: %s", exc)


async def emit_approval_denied(
    verifier: "Verifier | None",
    *,
    agent_id: str,
    trial_id: str,
    approval_id: str,
    decided_by: str | None,
    reason: str | None,
    resource: str,
    agent_name: str = "",
) -> None:
    """Emit ``approval.denied`` when a principal denies (or the request expires).

    Canonical Tier-1 payload:
    ``{approval_id, resource, decided_by?, reason?}``.

    Used both for explicit denials and for TTL-expired approvals; in
    the expired case ``decided_by`` will be empty and ``reason`` will
    be ``"expired"``.
    """
    if verifier is None:
        return
    payload: dict[str, Any] = {
        "approval_id": approval_id,
        "resource": resource,
    }
    if decided_by is not None:
        payload["decided_by"] = decided_by
    if reason is not None:
        payload["reason"] = reason
    agent = _build_verified_agent(agent_id=agent_id, agent_name=agent_name)
    try:
        await verifier.report_event(
            category="approval.denied",
            agent=agent,
            payload=payload,
            session_id=trial_id,
            outcome="failure",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_approval_denied: emission failed: %s", exc)


__all__ = [
    "emit_approval_denied",
    "emit_approval_granted",
    "emit_approval_requested",
    "emit_auth_deny",
    "emit_bet_decision",
    "emit_model_call",
    "emit_session_end",
    "emit_session_start",
    "emit_tool_use",
    "emit_transfer_value",
    "new_tool_invocation_id",
]
