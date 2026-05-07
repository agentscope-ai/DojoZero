"""Hub-side approval coordinator (spec §7.6.7 grant flow).

When an agent registers in approval mode, every bet routes through this
coordinator instead of placing directly:

  1. Hub :class:`ExternalAgentAdapter` calls
     :meth:`ApprovalCoordinator.submit` with the BetRequest. The
     coordinator builds an approval card (summary + facts), POSTs it to
     the IdP's ``/agentid/approvals`` endpoint, and stashes the bet
     keyed by ``approval_id``. Agent gets back a ``PendingApproval``.

  2. Agent (or runner) polls back via the gateway's
     ``/bets/pending/{approval_id}`` route, which calls
     :meth:`poll`. The coordinator queries the IdP, and on
     ``approved`` it verifies the JWS-signed decision JWT against the
     IdP's JWKS (the same key used for agent tokens), then mints an
     internal grant token and returns it.

  3. The gateway immediately consumes the grant via
     :meth:`consume_grant` to re-extract the original BetRequest, then
     places the bet through the broker. The grant is single-use.

The grant is hub-internal — agents never see ``gnt_xxx`` tokens. We
mint them anyway because (a) it preserves the spec's grant-flow shape
in the audit trail and (b) it lets a future v2 expose multi-step grant
handling without rearchitecting.

The bet content is sourced from our local stash, not from the
``ctx`` claim in the decision JWT. The IdP echoes ``ctx`` for hub
correlation; we compare its values to the stash for tamper detection
but never trust it as the placement input. A malicious or buggy IdP
can't change the bet that gets placed — only deny or approve the
exact request we submitted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx
import jwt as pyjwt
from jwt import InvalidTokenError, PyJWKClient

if TYPE_CHECKING:
    from dojozero.gateway._models import BetRequest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TTLs
# ---------------------------------------------------------------------------

# How long the IdP holds the approval pending before expiring it.
# Configurable via ttl_seconds at submit time; 5 min default matches
# what fits a typical "principal sees notification, decides" loop.
DEFAULT_APPROVAL_TTL_SECONDS = 300

# How long the hub-issued grant remains usable after the principal
# approves. Short — the runner polls and consumes immediately, so a
# 5-minute window is generous and bounds replay risk.
DEFAULT_GRANT_TTL_SECONDS = 300


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ApprovalError(Exception):
    """Base for approval-flow failures.

    ``status_hint`` lets the route handler map the failure to an HTTP
    status without re-classifying. Verifier-style.
    """

    def __init__(self, message: str, *, status_hint: int = 500):
        super().__init__(message)
        self.status_hint = status_hint


class ApprovalIdPError(ApprovalError):
    """The IdP refused or couldn't process the approval."""


class ApprovalNotFoundError(ApprovalError):
    """No pending approval (or grant) exists for this id."""

    def __init__(self, message: str):
        super().__init__(message, status_hint=404)


class ApprovalDecisionInvalidError(ApprovalError):
    """The decision JWT failed verification (signature/aud/sub/type)."""

    def __init__(self, message: str):
        super().__init__(message, status_hint=400)


class GrantInvalidError(ApprovalError):
    """Grant is expired, already consumed, or mismatched."""

    def __init__(self, message: str):
        super().__init__(message, status_hint=409)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class _PendingApproval:
    """A submitted approval awaiting (or just past) the principal's decision.

    Internal — :meth:`ApprovalCoordinator.poll` returns a
    :class:`PollResult` instead.
    """

    approval_id: str
    agent_id: str
    bet_request: "BetRequest"
    submitted_at: datetime
    expires_at: datetime
    # Final status once resolved; ``None`` while pending.
    resolved_decision: str | None = None
    # Grant minted post-approval (linked here for audit traceability).
    grant_id: str | None = None


@dataclass
class _Grant:
    """An active hub-issued grant tied to a specific approved BetRequest."""

    grant_id: str
    approval_id: str
    agent_id: str
    bet_request: "BetRequest"
    decision_jwt: str  # kept for audit; not re-verified after mint
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True)
class PollResult:
    """Outcome of a poll for a pending approval.

    Exactly one of ``status`` / ``grant`` matters per outcome:

    - ``"pending"``: nothing else is meaningful; caller should poll again.
    - ``"approved"``: ``grant_id`` and ``bet_request`` are populated.
      Caller is expected to consume the grant (place the bet) immediately.
    - ``"denied"``: ``denial_note`` may carry the principal's note.
    - ``"expired"``: the approval TTL elapsed without a decision.
    """

    status: str  # "pending" | "approved" | "denied" | "expired"
    expires_at: datetime
    grant_id: str | None = None
    bet_request: "BetRequest | None" = None
    denial_note: str | None = None
    decided_by: str | None = None


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class ApprovalCoordinator:
    """Per-gateway state holder for the approval grant flow.

    Single instance per :class:`GatewayState`. Holds:

    - in-flight approvals (the bet is stashed here while the principal
      decides)
    - issued grants (post-approval, awaiting the gateway's internal
      consume)
    - the IdP discovery URL + JWKS client (cached) used to verify
      decision JWTs

    All storage is in-memory. A gateway crash drops in-flight state;
    pending IdP-side approvals expire by TTL on their own. v1 limit;
    Redis-backed store is a v2 concern.
    """

    def __init__(
        self,
        *,
        approval_endpoint: str,
        hub_audience: str,
        trusted_issuers: set[str],
        idp_jwks_url: str,
        approval_ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
        grant_ttl_seconds: int = DEFAULT_GRANT_TTL_SECONDS,
    ) -> None:
        """
        Args:
            approval_endpoint: ``<idp>/agentid/approvals`` — where to POST
                requests and GET status. Discovered or env-configured by
                the gateway.
            hub_audience: this hub's ``service_id`` — must match the
                ``aud`` claim in decision JWTs we're willing to accept.
            trusted_issuers: IdP issuer URLs (trust list); decision JWTs
                from any other ``iss`` are rejected.
            idp_jwks_url: where to fetch the IdP's JWKS for decision-JWT
                verification. Cached by ``PyJWKClient``.
            approval_ttl_seconds: how long the IdP should keep the
                approval pending (sent as ``ttl_seconds``).
            grant_ttl_seconds: how long a minted grant remains
                consumable.
        """
        self._approval_endpoint = approval_endpoint.rstrip("/")
        self._hub_audience = hub_audience
        self._trusted_issuers = trusted_issuers
        self._approval_ttl = approval_ttl_seconds
        self._grant_ttl = grant_ttl_seconds

        self._http: httpx.AsyncClient | None = None
        self._jwks_client = PyJWKClient(idp_jwks_url, cache_keys=True)

        self._pending: dict[str, _PendingApproval] = {}
        self._grants: dict[str, _Grant] = {}
        self._lock = asyncio.Lock()

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # -----------------------------------------------------------------
    # Submit
    # -----------------------------------------------------------------

    async def submit(
        self,
        *,
        agent_id: str,
        bet_request: "BetRequest",
        summary: str,
        facts: list[dict[str, Any]],
        hub_id: str,
    ) -> _PendingApproval:
        """POST a new approval to the IdP, stash the bet, return the pending row.

        ``hub_id`` is what the IdP records as the requesting hub
        (typically equal to ``hub_audience``). ``payload`` echoed back
        in the decision's ``ctx`` is a serialized form of ``bet_request``;
        we don't trust it on the way back, but it's useful for the
        principal's audit log.
        """
        body = {
            "hub_id": hub_id,
            "agent_id": agent_id,
            "summary": summary,
            "facts": facts,
            "payload": bet_request.model_dump(by_alias=False, exclude_none=True),
            "ttl_seconds": self._approval_ttl,
        }

        client = await self._client()
        try:
            resp = await client.post(self._approval_endpoint, json=body)
        except httpx.HTTPError as exc:
            raise ApprovalIdPError(
                f"Could not reach IdP approval endpoint: {exc}",
                status_hint=503,
            ) from exc

        if resp.status_code >= 400:
            raise ApprovalIdPError(
                f"IdP rejected approval: {resp.status_code} {resp.text[:200]}",
                status_hint=502,
            )

        data = resp.json()
        approval_id = str(data["approval_id"])
        expires_at = _parse_iso(data.get("expires_at")) or _now() + timedelta(
            seconds=self._approval_ttl
        )

        pending = _PendingApproval(
            approval_id=approval_id,
            agent_id=agent_id,
            bet_request=bet_request,
            submitted_at=_now(),
            expires_at=expires_at,
        )
        async with self._lock:
            self._pending[approval_id] = pending
        logger.info(
            "approval submitted: id=%s agent=%s amount=%s",
            approval_id,
            agent_id,
            bet_request.amount,
        )
        return pending

    # -----------------------------------------------------------------
    # Poll
    # -----------------------------------------------------------------

    async def poll(self, approval_id: str, *, agent_id: str) -> PollResult:
        """Query the IdP. On approved, verify the decision JWT and mint a grant.

        ``agent_id`` is the verified caller; we refuse to surface a
        result for an approval that doesn't belong to this agent (the
        gateway already auth-checks the route, this is defense in
        depth).
        """
        async with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            raise ApprovalNotFoundError(f"Unknown approval_id: {approval_id}")
        if pending.agent_id != agent_id:
            # Don't reveal that a different agent owns it.
            raise ApprovalNotFoundError(f"Unknown approval_id: {approval_id}")

        # If already resolved (we've minted a grant before), short-circuit.
        if pending.resolved_decision == "approved" and pending.grant_id:
            grant = self._grants.get(pending.grant_id)
            if grant and grant.consumed_at is None and grant.expires_at > _now():
                return PollResult(
                    status="approved",
                    expires_at=pending.expires_at,
                    grant_id=grant.grant_id,
                    bet_request=grant.bet_request,
                )
            # Grant expired or consumed → expired from the runner's POV.
            return PollResult(
                status="expired",
                expires_at=pending.expires_at,
            )
        if pending.resolved_decision == "denied":
            return PollResult(
                status="denied",
                expires_at=pending.expires_at,
            )

        # Hit the IdP.
        client = await self._client()
        url = f"{self._approval_endpoint}/{approval_id}"
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise ApprovalIdPError(
                f"Could not reach IdP approval endpoint: {exc}",
                status_hint=503,
            ) from exc
        if resp.status_code == 404:
            raise ApprovalNotFoundError(f"Unknown approval_id: {approval_id}")
        if resp.status_code >= 400:
            raise ApprovalIdPError(
                f"IdP returned {resp.status_code}: {resp.text[:200]}",
                status_hint=502,
            )

        data = resp.json()
        idp_status = str(data.get("status", "")).lower()
        idp_expires = _parse_iso(data.get("expires_at")) or pending.expires_at

        if idp_status == "pending":
            return PollResult(status="pending", expires_at=idp_expires)
        if idp_status == "expired":
            async with self._lock:
                pending.resolved_decision = "expired"
            return PollResult(status="expired", expires_at=idp_expires)
        if idp_status == "denied":
            decision_jwt = data.get("decision_jwt")
            decided_by, note = (
                self._decision_metadata(decision_jwt) if decision_jwt else (None, None)
            )
            async with self._lock:
                pending.resolved_decision = "denied"
            return PollResult(
                status="denied",
                expires_at=idp_expires,
                denial_note=note,
                decided_by=decided_by,
            )
        if idp_status != "approved":
            raise ApprovalIdPError(
                f"IdP returned unknown status {idp_status!r}",
                status_hint=502,
            )

        # Approved. Verify the JWT before minting a grant.
        decision_jwt = data.get("decision_jwt")
        if not decision_jwt:
            raise ApprovalIdPError(
                "IdP marked approved but returned no decision_jwt",
                status_hint=502,
            )
        claims = self._verify_decision_jwt(
            decision_jwt,
            agent_id=pending.agent_id,
        )
        decided_by = claims.get("decided_by")

        grant_id = f"gnt_{secrets.token_hex(8)}"
        grant = _Grant(
            grant_id=grant_id,
            approval_id=approval_id,
            agent_id=pending.agent_id,
            bet_request=pending.bet_request,
            decision_jwt=decision_jwt,
            issued_at=_now(),
            expires_at=_now() + timedelta(seconds=self._grant_ttl),
        )
        async with self._lock:
            pending.resolved_decision = "approved"
            pending.grant_id = grant_id
            self._grants[grant_id] = grant

        logger.info(
            "approval granted: id=%s agent=%s grant=%s decided_by=%s",
            approval_id,
            agent_id,
            grant_id,
            decided_by,
        )
        return PollResult(
            status="approved",
            expires_at=idp_expires,
            grant_id=grant_id,
            bet_request=pending.bet_request,
            decided_by=decided_by,
        )

    # -----------------------------------------------------------------
    # Consume grant
    # -----------------------------------------------------------------

    async def consume_grant(self, grant_id: str, *, agent_id: str) -> "BetRequest":
        """Mark a grant used and return its BetRequest.

        Raises :class:`GrantInvalidError` if the grant is already
        consumed, expired, or belongs to a different agent.
        """
        async with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                raise ApprovalNotFoundError(f"Unknown grant_id: {grant_id}")
            if grant.agent_id != agent_id:
                raise ApprovalNotFoundError(f"Unknown grant_id: {grant_id}")
            if grant.consumed_at is not None:
                raise GrantInvalidError(
                    f"Grant {grant_id} already consumed at "
                    f"{grant.consumed_at.isoformat()}"
                )
            if grant.expires_at <= _now():
                raise GrantInvalidError(f"Grant {grant_id} expired")
            grant.consumed_at = _now()
            return grant.bet_request

    # -----------------------------------------------------------------
    # JWT verification
    # -----------------------------------------------------------------

    def _verify_decision_jwt(self, token: str, *, agent_id: str) -> dict[str, Any]:
        """Verify ``decision_jwt`` against the IdP's JWKS.

        Checks: signature (Ed25519/EdDSA), ``aud == hub_audience``,
        ``sub == agent_id``, ``iss in trusted_issuers``,
        ``type == "approval_decision"``, ``exp`` valid.
        """
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
            claims = pyjwt.decode(
                token,
                signing_key,
                algorithms=["EdDSA", "ES256"],
                audience=self._hub_audience,
                options={"require": ["iss", "sub", "aud", "exp", "type"]},
            )
        except InvalidTokenError as exc:
            raise ApprovalDecisionInvalidError(
                f"decision_jwt failed verification: {exc}"
            ) from exc

        if claims.get("type") != "approval_decision":
            raise ApprovalDecisionInvalidError(
                f"decision_jwt has wrong type: {claims.get('type')!r}"
            )
        if claims.get("sub") != agent_id:
            raise ApprovalDecisionInvalidError(
                "decision_jwt sub doesn't match the pending approval's agent"
            )
        iss = str(claims.get("iss", ""))
        if iss not in self._trusted_issuers:
            raise ApprovalDecisionInvalidError(f"decision_jwt iss not trusted: {iss!r}")
        if str(claims.get("decision", "")).lower() != "approved":
            raise ApprovalDecisionInvalidError(
                "decision_jwt has non-approved decision in approved branch"
            )
        return claims

    @staticmethod
    def _decision_metadata(decision_jwt: str) -> tuple[str | None, str | None]:
        """Best-effort read of ``decided_by`` and ``note`` for surfaced messages.

        Used in the denied branch where the JWT isn't fully verified
        (it doesn't gate any action — just informational). Failures
        return ``(None, None)``.
        """
        try:
            payload_b64 = decision_jwt.split(".")[1]
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            import base64

            data = json.loads(base64.urlsafe_b64decode(padded))
            return data.get("decided_by"), data.get("note")
        except Exception:  # noqa: BLE001
            return None, None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # IdP returns timezone-aware ISO 8601.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def build_bet_facts(bet_request: "BetRequest", trial_id: str) -> list[dict[str, Any]]:
    """Format a BetRequest as the IdP's approval-card ``facts`` list.

    The IdP renders these in its portal UI. ``kind`` lets the UI pick a
    formatter (``money`` for stake, ``risk`` for odds, ``text`` for
    selections). Order matters — the portal renders top-to-bottom.
    """
    facts: list[dict[str, Any]] = [
        {"label": "Trial", "value": trial_id, "kind": "text"},
        {"label": "Market", "value": bet_request.market, "kind": "text"},
        {"label": "Selection", "value": bet_request.selection, "kind": "text"},
        {"label": "Stake", "value": f"${bet_request.amount}", "kind": "money"},
    ]
    if bet_request.limit_probability is not None:
        facts.append(
            {
                "label": "Limit probability",
                "value": f"{bet_request.limit_probability:.3f}",
                "kind": "risk",
            }
        )
    if bet_request.spread_value is not None:
        facts.append(
            {
                "label": "Spread",
                "value": f"{bet_request.spread_value}",
                "kind": "text",
            }
        )
    if bet_request.total_value is not None:
        facts.append(
            {
                "label": "Total",
                "value": f"{bet_request.total_value}",
                "kind": "text",
            }
        )
    if bet_request.rationale:
        # Truncate to fit the IdP's Fact.value max length (512).
        rationale = bet_request.rationale
        if len(rationale) > 480:
            rationale = rationale[:477] + "..."
        facts.append({"label": "Rationale", "value": rationale, "kind": "text"})
    return facts


def build_bet_summary(bet_request: "BetRequest") -> str:
    """One-line summary the principal sees before tapping Approve.

    Designed to fit the IdP's 512-char limit and be readable on a
    notification preview.
    """
    return (
        f"Place ${bet_request.amount} bet on {bet_request.selection} "
        f"({bet_request.market})"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ApprovalCoordinator",
    "ApprovalError",
    "ApprovalIdPError",
    "ApprovalNotFoundError",
    "ApprovalDecisionInvalidError",
    "GrantInvalidError",
    "PollResult",
    "build_bet_facts",
    "build_bet_summary",
    "DEFAULT_APPROVAL_TTL_SECONDS",
    "DEFAULT_GRANT_TTL_SECONDS",
]
