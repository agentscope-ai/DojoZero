"""Tests for AgentID activity event emission helpers.

Covers ``gateway/_activity.py`` — best-effort emitters for ``auth.deny``,
``session.start``, ``session.end``. Verifier internals are mocked; we
just assert which methods got called and with what shape.
"""

from __future__ import annotations

import time

import jwt
import pytest

from dojozero.gateway._activity import (
    emit_auth_deny,
    emit_session_end,
    emit_session_start,
    emit_transfer_value,
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
# emit_auth_deny
# ---------------------------------------------------------------------------


class TestEmitAuthDeny:
    @pytest.mark.asyncio
    async def test_no_op_when_verifier_is_none(self):
        # Just shouldn't raise.
        await emit_auth_deny(
            None, token="anything", route="/balance", reason="token_expired"
        )

    @pytest.mark.asyncio
    async def test_extracts_unverified_claims(self):
        verifier = _make_verifier_mock()
        token = jwt.encode(
            {
                "sub": "agentid:dojozero:degen-claude",
                "iss": "https://pre.agent-id.live",
                "aud": "https://api.dojozero.live",
                "exp": int(time.time()) - 60,  # already expired (we're emitting deny)
            },
            "secret",
            algorithm="HS256",
            headers={"kid": "kid-abc"},
        )

        await emit_auth_deny(
            verifier, token=token, route="/bets", reason="token_expired"
        )

        verifier.report_event.assert_awaited_once()
        kwargs = verifier.report_event.await_args.kwargs
        assert kwargs["category"] == "auth.deny"
        assert kwargs["outcome"] == "failure"
        assert kwargs["route"] == "/bets"
        assert kwargs["payload"] == {"route": "/bets", "reason": "token_expired"}
        agent = kwargs["agent"]
        assert agent.agent_id == "agentid:dojozero:degen-claude"
        assert agent.issuer == "https://pre.agent-id.live"
        assert agent.raw_claims.get("_kid") == "kid-abc"

    @pytest.mark.asyncio
    async def test_garbage_token_still_emits_with_empty_identity(self):
        """A token so malformed we can't even parse should still emit an event."""
        verifier = _make_verifier_mock()
        await emit_auth_deny(
            verifier,
            token="not.a.jwt",
            route="/balance",
            reason="signature_invalid",
        )
        verifier.report_event.assert_awaited_once()
        agent = verifier.report_event.await_args.kwargs["agent"]
        assert agent.agent_id == "unknown"

    @pytest.mark.asyncio
    async def test_emission_failure_is_swallowed(self, caplog):
        """If the activity service rejects the event, the gateway must not break."""
        from unittest.mock import AsyncMock

        verifier = _make_verifier_mock()
        verifier.report_event = AsyncMock(side_effect=RuntimeError("activity-down"))

        with caplog.at_level("WARNING"):
            await emit_auth_deny(
                verifier, token="x.y.z", route="/balance", reason="token_invalid"
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
    async def test_passes_through_session_id_and_extras(self):
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
        assert kwargs["trial_id"] == "trial-canary-001"
        assert kwargs["persona"] == "degen"
        assert kwargs["model"] == "qwen3-max"
        assert kwargs["sport_type"] == "nba"

    @pytest.mark.asyncio
    async def test_omits_unset_optional_fields(self):
        verifier = _make_verifier_mock()
        await emit_session_start(verifier, agent_id="agentid:dojozero:x", trial_id="t1")
        kwargs = verifier.report_session_start.await_args.kwargs
        assert "persona" not in kwargs
        assert "model" not in kwargs
        assert "sport_type" not in kwargs


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
    async def test_includes_duration_and_optional_payload(self):
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
        assert kwargs["final_balance"] == "1234.56"
        assert kwargs["last_observed_sequence"] == 812
        assert kwargs["bet_count"] == 12

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
            market="moneyline",
            selection="home",
            bet_id="bet_1",
        )

    @pytest.mark.asyncio
    async def test_emits_with_full_payload(self):
        verifier = _make_verifier_mock()
        await emit_transfer_value(
            verifier,
            agent_id="agentid:dojozero:degen",
            agent_name="Danny Hype",
            trial_id="trial-canary-001",
            amount="50.00",
            market="moneyline",
            selection="home",
            bet_id="bet_42",
            event_id="lakers_warriors_2026_05_04",
            probability="0.55",
            shares="90.91",
            reference_sequence=812,
        )

        verifier.report_event.assert_awaited_once()
        kwargs = verifier.report_event.await_args.kwargs
        assert kwargs["category"] == "transfer.value"
        assert kwargs["outcome"] == "success"
        assert kwargs["session_id"] == "trial-canary-001"
        payload = kwargs["payload"]
        assert payload == {
            "trial_id": "trial-canary-001",
            "amount": "50.00",
            "market": "moneyline",
            "selection": "home",
            "bet_id": "bet_42",
            "event_id": "lakers_warriors_2026_05_04",
            "probability": "0.55",
            "shares": "90.91",
            "reference_sequence": 812,
        }

    @pytest.mark.asyncio
    async def test_omits_unset_optional_fields(self):
        verifier = _make_verifier_mock()
        await emit_transfer_value(
            verifier,
            agent_id="agentid:dojozero:x",
            trial_id="t1",
            amount="10",
            market="spread",
            selection="away",
            bet_id="bet_1",
        )
        payload = verifier.report_event.await_args.kwargs["payload"]
        assert "probability" not in payload
        assert "shares" not in payload
        assert "reference_sequence" not in payload
        assert "event_id" not in payload

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
                market="moneyline",
                selection="home",
                bet_id="bet_1",
            )
        assert any(
            "emit_transfer_value" in r.message and "activity-down" in r.message
            for r in caplog.records
        )
