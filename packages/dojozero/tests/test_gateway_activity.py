"""Tests for AgentID activity event emission helpers.

Covers ``gateway/_activity.py`` — best-effort emitters for ``auth.deny``,
``session.start``, ``session.end``, ``transfer.value``. Verifier
internals are mocked; we just assert which methods got called and with
what shape.

Payload shapes match aip-activity's canonical Tier-1 schemas in
``aip-activity/app/schemas/categories.py``. Hub-specific richness lives
in ``attributes`` / ``summary`` open dicts (sessions) or in linked
Tier-2 events (transfer.value via ``transaction_id`` /
``linked_tier2``).
"""

from __future__ import annotations

import time

import jwt
import pytest

from dojozero.gateway._activity import (
    _amount_bucket,
    _hash_args,
    emit_auth_deny,
    emit_session_end,
    emit_session_start,
    emit_tool_use,
    emit_transfer_value,
    new_tool_invocation_id,
)


def _make_verifier_mock():
    """Build a verifier double with awaitable report_* methods."""
    from unittest.mock import AsyncMock

    verifier = AsyncMock()
    verifier.report_event = AsyncMock(return_value=None)
    verifier.report_session_start = AsyncMock(return_value=None)
    verifier.report_session_end = AsyncMock(return_value=None)
    return verifier


# ---------------------------------------------------------------------------
# _amount_bucket
# ---------------------------------------------------------------------------


class TestAmountBucket:
    """Mirrors aip-activity's AMOUNT_BUCKETS whitelist."""

    @pytest.mark.parametrize(
        "amount,bucket",
        [
            ("0", "lt_100"),
            ("99.99", "lt_100"),
            ("100", "100_1k"),
            ("999", "100_1k"),
            ("1000", "1k_10k"),
            ("9999", "1k_10k"),
            ("10000", "10k_plus"),
            ("100000", "10k_plus"),
        ],
    )
    def test_buckets(self, amount, bucket):
        assert _amount_bucket(amount) == bucket

    def test_unparseable_falls_back_to_lt_100(self):
        assert _amount_bucket("not-a-number") == "lt_100"
        assert _amount_bucket(None) == "lt_100"  # type: ignore[arg-type]

    def test_negative_falls_back_to_lt_100(self):
        # Defensive: negative amounts shouldn't happen but won't crash.
        assert _amount_bucket("-50") == "lt_100"


# ---------------------------------------------------------------------------
# emit_auth_deny
# ---------------------------------------------------------------------------


class TestEmitAuthDeny:
    @pytest.mark.asyncio
    async def test_no_op_when_verifier_is_none(self):
        await emit_auth_deny(
            None, token="anything", route="/balance", error_class="token_expired"
        )

    @pytest.mark.asyncio
    async def test_extracts_unverified_claims(self):
        verifier = _make_verifier_mock()
        token = jwt.encode(
            {
                "sub": "agentid:dojozero:degen-claude",
                "iss": "https://pre.agent-id.live",
                "aud": "https://api.dojozero.live",
                "exp": int(time.time()) - 60,
            },
            "secret",
            algorithm="HS256",
            headers={"kid": "kid-abc"},
        )

        await emit_auth_deny(
            verifier, token=token, route="/bets", error_class="token_expired"
        )

        verifier.report_event.assert_awaited_once()
        kwargs = verifier.report_event.await_args.kwargs
        assert kwargs["category"] == "auth.deny"
        assert kwargs["outcome"] == "failure"
        assert kwargs["route"] == "/bets"
        # Canonical Tier-1 shape: {route, error_class}
        assert kwargs["payload"] == {
            "route": "/bets",
            "error_class": "token_expired",
        }
        agent = kwargs["agent"]
        assert agent.agent_id == "agentid:dojozero:degen-claude"
        assert agent.issuer == "https://pre.agent-id.live"
        assert agent.raw_claims.get("_kid") == "kid-abc"

    @pytest.mark.asyncio
    async def test_garbage_token_still_emits_with_empty_identity(self):
        verifier = _make_verifier_mock()
        await emit_auth_deny(
            verifier,
            token="not.a.jwt",
            route="/balance",
            error_class="signature_invalid",
        )
        verifier.report_event.assert_awaited_once()
        agent = verifier.report_event.await_args.kwargs["agent"]
        assert agent.agent_id == "unknown"

    @pytest.mark.asyncio
    async def test_emission_failure_is_swallowed(self, caplog):
        from unittest.mock import AsyncMock

        verifier = _make_verifier_mock()
        verifier.report_event = AsyncMock(side_effect=RuntimeError("activity-down"))

        with caplog.at_level("WARNING"):
            await emit_auth_deny(
                verifier,
                token="x.y.z",
                route="/balance",
                error_class="token_invalid",
            )
        assert any(
            "emit_auth_deny" in r.message and "activity-down" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# emit_session_start
# ---------------------------------------------------------------------------


class TestEmitSessionStart:
    @pytest.mark.asyncio
    async def test_no_op_when_verifier_is_none(self):
        await emit_session_start(
            None, agent_id="agentid:dojozero:x", trial_id="trial-1"
        )

    @pytest.mark.asyncio
    async def test_passes_canonical_fields_and_attributes(self):
        verifier = _make_verifier_mock()
        await emit_session_start(
            verifier,
            agent_id="agentid:dojozero:degen",
            agent_name="Danny Hype",
            trial_id="trial-canary-001",
            persona="degen",
            model="qwen3-max",
            sport_type="nba",
        )

        verifier.report_session_start.assert_awaited_once()
        args, kwargs = (
            verifier.report_session_start.await_args.args,
            verifier.report_session_start.await_args.kwargs,
        )
        agent = args[0]
        assert agent.agent_id == "agentid:dojozero:degen"
        assert kwargs["session_id"] == "trial-canary-001"
        # scenario is a canonical Tier-1 field; "trial" is DojoZero's label
        # for a betting-trial session.
        assert kwargs["scenario"] == "trial"
        # Hub-specific richness lives in `attributes`, not at payload top
        # level — that's the canonical-vs-domain split.
        assert kwargs["attributes"] == {
            "persona": "degen",
            "model": "qwen3-max",
            "sport_type": "nba",
        }

    @pytest.mark.asyncio
    async def test_no_attributes_when_no_extras(self):
        verifier = _make_verifier_mock()
        await emit_session_start(verifier, agent_id="agentid:dojozero:x", trial_id="t1")
        kwargs = verifier.report_session_start.await_args.kwargs
        # scenario always set; attributes only if non-empty
        assert kwargs["scenario"] == "trial"
        assert "attributes" not in kwargs


# ---------------------------------------------------------------------------
# emit_session_end
# ---------------------------------------------------------------------------


class TestEmitSessionEnd:
    @pytest.mark.asyncio
    async def test_no_op_when_verifier_is_none(self):
        await emit_session_end(
            None,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            duration_ms=1000,
        )

    @pytest.mark.asyncio
    async def test_includes_outcome_and_summary_dict(self):
        verifier = _make_verifier_mock()
        await emit_session_end(
            verifier,
            agent_id="agentid:dojozero:degen",
            trial_id="trial-canary-001",
            duration_ms=5_400_000,  # 90 min
            final_balance="1234.56",
            last_observed_sequence=812,
            bet_count=12,
        )
        kwargs = verifier.report_session_end.await_args.kwargs
        assert kwargs["session_id"] == "trial-canary-001"
        assert kwargs["duration_ms"] == 5_400_000
        # outcome is a canonical Tier-1 field
        assert kwargs["outcome"] == "completed"
        # Hub-specific roll-ups live in `summary`, not at payload top level
        assert kwargs["summary"] == {
            "final_balance": "1234.56",
            "last_observed_sequence": 812,
            "bet_count": 12,
        }

    @pytest.mark.asyncio
    async def test_outcome_override(self):
        verifier = _make_verifier_mock()
        await emit_session_end(
            verifier,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            duration_ms=1000,
            outcome="cancelled",
        )
        assert verifier.report_session_end.await_args.kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_no_summary_when_no_extras(self):
        verifier = _make_verifier_mock()
        await emit_session_end(
            verifier,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            duration_ms=1000,
        )
        kwargs = verifier.report_session_end.await_args.kwargs
        assert "summary" not in kwargs

    @pytest.mark.asyncio
    async def test_negative_duration_clamped_to_zero(self):
        verifier = _make_verifier_mock()
        await emit_session_end(
            verifier,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            duration_ms=-100,
        )
        assert verifier.report_session_end.await_args.kwargs["duration_ms"] == 0


# ---------------------------------------------------------------------------
# emit_transfer_value
# ---------------------------------------------------------------------------


class TestEmitTransferValue:
    @pytest.mark.asyncio
    async def test_no_op_when_verifier_is_none(self):
        await emit_transfer_value(
            None,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            amount="100",
            bet_id="bet_1",
        )

    @pytest.mark.asyncio
    async def test_canonical_payload_shape(self):
        verifier = _make_verifier_mock()
        await emit_transfer_value(
            verifier,
            agent_id="agentid:dojozero:degen",
            agent_name="Danny Hype",
            trial_id="trial-canary-001",
            amount="50.00",
            bet_id="bet_42",
        )

        verifier.report_event.assert_awaited_once()
        kwargs = verifier.report_event.await_args.kwargs
        assert kwargs["category"] == "transfer.value"
        assert kwargs["outcome"] == "success"
        assert kwargs["session_id"] == "trial-canary-001"
        payload = kwargs["payload"]
        # Canonical Tier-1 fields, not hub-specific richness
        assert payload["amount_bucket"] == "lt_100"
        assert payload["currency"] == "USD"
        assert payload["direction"] == "out"
        assert payload["amount"] == 50.0
        assert payload["purpose"] == "stake"
        assert payload["transaction_id"] == "bet_42"
        # Forward reference to the Tier-2 event that carries hub-specific
        # richness (market, selection, etc.) — not yet emitted.
        assert payload["linked_tier2"] == "dojozero.bet_decision"
        # Hub-specific fields explicitly NOT in canonical Tier-1 payload
        assert "market" not in payload
        assert "selection" not in payload

    @pytest.mark.asyncio
    async def test_amount_bucket_derived_from_amount(self):
        verifier = _make_verifier_mock()
        await emit_transfer_value(
            verifier,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            amount="500",
            bet_id="bet_x",
        )
        payload = verifier.report_event.await_args.kwargs["payload"]
        assert payload["amount_bucket"] == "100_1k"

    @pytest.mark.asyncio
    async def test_counterparty_optional(self):
        verifier = _make_verifier_mock()
        await emit_transfer_value(
            verifier,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            amount="100",
            bet_id="bet_x",
            counterparty_principal_id="agentid:dojozero:broker",
        )
        payload = verifier.report_event.await_args.kwargs["payload"]
        assert payload["counterparty_principal_id"] == "agentid:dojozero:broker"

    @pytest.mark.asyncio
    async def test_linked_tier2_can_be_disabled(self):
        verifier = _make_verifier_mock()
        await emit_transfer_value(
            verifier,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            amount="100",
            bet_id="bet_x",
            linked_tier2=None,
        )
        payload = verifier.report_event.await_args.kwargs["payload"]
        assert "linked_tier2" not in payload

    @pytest.mark.asyncio
    async def test_emission_failure_swallowed(self, caplog):
        from unittest.mock import AsyncMock

        verifier = _make_verifier_mock()
        verifier.report_event = AsyncMock(side_effect=RuntimeError("activity-down"))

        with caplog.at_level("WARNING"):
            await emit_transfer_value(
                verifier,
                agent_id="agentid:dojozero:x",
                trial_id="t1",
                amount="100",
                bet_id="bet_x",
            )
        assert any(
            "emit_transfer_value" in r.message and "activity-down" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# emit_tool_use
# ---------------------------------------------------------------------------


class TestHashArgs:
    def test_canonical_form_stable_across_key_order(self):
        a = _hash_args({"market": "moneyline", "stake": "100"})
        b = _hash_args({"stake": "100", "market": "moneyline"})
        assert a == b
        assert a.startswith("sha256:")
        assert len(a) == len("sha256:") + 64

    def test_different_args_different_hash(self):
        assert _hash_args({"x": 1}) != _hash_args({"x": 2})

    def test_empty_and_none_hash_to_same(self):
        assert _hash_args({}) == _hash_args(None)


class TestEmitToolUse:
    @pytest.mark.asyncio
    async def test_no_op_when_verifier_is_none_returns_invocation_id(self):
        result = await emit_tool_use(
            None,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            tool_name="dojozero.place_bet",
            args={"stake": "100"},
        )
        # Caller still gets an id even when emission is disabled, so
        # downstream linked_transfer_id wiring works in dev/local mode.
        assert result.startswith("inv_")

    @pytest.mark.asyncio
    async def test_canonical_payload_shape(self):
        verifier = _make_verifier_mock()
        invocation_id = await emit_tool_use(
            verifier,
            agent_id="agentid:dojozero:degen",
            agent_name="Danny Hype",
            trial_id="trial-canary-001",
            tool_name="dojozero.place_bet",
            args={"market": "moneyline", "selection": "home"},
            duration_ms=42,
            success=True,
            linked_transfer_id="bet_42",
        )

        verifier.report_event.assert_awaited_once()
        kwargs = verifier.report_event.await_args.kwargs
        assert kwargs["category"] == "tool.use"
        assert kwargs["outcome"] == "success"
        assert kwargs["session_id"] == "trial-canary-001"
        payload = kwargs["payload"]
        assert payload["tool_name"] == "dojozero.place_bet"
        assert payload["args_hash"].startswith("sha256:")
        assert payload["tool_invocation_id"] == invocation_id
        assert payload["duration_ms"] == 42
        assert payload["success"] is True
        assert payload["linked_transfer_id"] == "bet_42"
        # Raw args MUST NOT leak.
        assert "market" not in payload
        assert "selection" not in payload

    @pytest.mark.asyncio
    async def test_failure_outcome(self):
        verifier = _make_verifier_mock()
        await emit_tool_use(
            verifier,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            tool_name="dojozero.place_bet",
            args={"stake": "999999"},
            duration_ms=5,
            success=False,
        )
        kwargs = verifier.report_event.await_args.kwargs
        assert kwargs["outcome"] == "failure"
        assert kwargs["payload"]["success"] is False
        # No linked_transfer_id on failure (no transfer happened).
        assert "linked_transfer_id" not in kwargs["payload"]

    @pytest.mark.asyncio
    async def test_caller_supplied_invocation_id_passes_through(self):
        verifier = _make_verifier_mock()
        result = await emit_tool_use(
            verifier,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            tool_name="dojozero.place_bet",
            tool_invocation_id="inv_explicit_42",
        )
        assert result == "inv_explicit_42"
        kwargs = verifier.report_event.await_args.kwargs
        assert kwargs["payload"]["tool_invocation_id"] == "inv_explicit_42"

    @pytest.mark.asyncio
    async def test_negative_duration_clamped(self):
        verifier = _make_verifier_mock()
        await emit_tool_use(
            verifier,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            tool_name="dojozero.place_bet",
            duration_ms=-5,
        )
        payload = verifier.report_event.await_args.kwargs["payload"]
        assert payload["duration_ms"] == 0

    @pytest.mark.asyncio
    async def test_emission_failure_swallowed(self, caplog):
        from unittest.mock import AsyncMock

        verifier = _make_verifier_mock()
        verifier.report_event = AsyncMock(side_effect=RuntimeError("activity-down"))
        with caplog.at_level("WARNING"):
            invocation_id = await emit_tool_use(
                verifier,
                agent_id="agentid:dojozero:x",
                trial_id="t1",
                tool_name="dojozero.place_bet",
            )
        # Caller still gets the id even when emission failed.
        assert invocation_id.startswith("inv_")
        assert any(
            "emit_tool_use" in r.message and "activity-down" in r.message
            for r in caplog.records
        )

    def test_new_tool_invocation_id_is_unique(self):
        ids = {new_tool_invocation_id() for _ in range(100)}
        assert len(ids) == 100
        assert all(i.startswith("inv_") for i in ids)
