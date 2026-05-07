"""Agentscope tool functions backed by ``dojozero-client``.

Each builder takes a live ``TrialConnection`` and returns an async function
shaped for ``Toolkit.register_tool_function``. The agent calls them through
the ReAct loop; the LLM never touches the SDK directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from dojozero_client import BetResult, PendingApproval, PendingBetStatus

if TYPE_CHECKING:
    from dojozero_client import TrialConnection


def build_tools(
    connection: "TrialConnection",
    *,
    request_approval: bool = False,
) -> list:
    """Return a list of agentscope-compatible tool callables for ``connection``.

    Each callable closes over the live connection so the LLM only needs to
    pass the action-shaped kwargs (market, selection, amount, etc.). The
    tools are intentionally narrow — the canary only needs the actions a
    persona uses to react to a live game.

    When ``request_approval`` is True, also registers ``check_pending_bet``
    so the agent can poll IdP-mediated approvals (spec §7.6.7) and the
    ``place_bet`` tool's docstring tells the LLM about the pending case.
    """
    tools: list = [
        _make_get_balance(connection),
        _make_get_current_odds(connection),
        _make_place_bet(connection, approval_mode=request_approval),
    ]
    if request_approval:
        tools.append(_make_check_pending_bet(connection))
    return tools


def _ok(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _err(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=f"ERROR: {text}")])


def _make_get_balance(connection: "TrialConnection"):
    async def get_balance() -> ToolResponse:
        """Fetch the agent's current broker balance for this trial.

        Use this to size bets responsibly. Returns balance and the last
        event sequence the agent has observed (useful as the staleness
        guard when placing a follow-up bet).
        """
        try:
            balance = await connection.get_balance()
        except Exception as exc:  # noqa: BLE001
            return _err(f"failed to fetch balance: {exc}")
        return _ok(
            f"balance={balance.balance}, last_sequence={connection.last_sequence}"
        )

    return get_balance


def _make_get_current_odds(connection: "TrialConnection"):
    async def get_current_odds() -> ToolResponse:
        """Fetch the most recent odds snapshot from the gateway.

        Returns the latest home / away win probabilities, whether betting
        is currently open, and the snapshot's sequence number. Use the
        sequence as ``reference_sequence`` when placing a bet so the
        broker can reject the trade if odds have moved underneath us.
        """
        try:
            odds = await connection.get_current_odds()
        except Exception as exc:  # noqa: BLE001
            return _err(f"failed to fetch odds: {exc}")
        return _ok(
            f"sequence={odds.sequence}, "
            f"home_probability={odds.home_probability}, "
            f"away_probability={odds.away_probability}, "
            f"betting_open={odds.betting_open}"
        )

    return get_current_odds


def _make_place_bet(connection: "TrialConnection", *, approval_mode: bool):
    if approval_mode:

        async def place_bet(
            market: str,
            selection: str,
            amount: float,
            reference_sequence: int,
        ) -> ToolResponse:
            """Submit a bet for principal approval.

            This agent runs in approval mode: bets do NOT place
            immediately. The principal must approve via the IdP portal
            before the broker accepts the trade. Submission returns an
            ``approval_id`` that you must pass to ``check_pending_bet``
            to discover whether the principal approved, denied, or let
            the request expire.

            Args:
                market: ``"moneyline"`` / ``"spread"`` / ``"total"``.
                selection: ``"home"`` / ``"away"`` (moneyline, spread)
                    or ``"over"`` / ``"under"`` (total).
                amount: Stake size.
                reference_sequence: Odds snapshot sequence.
            """
            try:
                result = await connection.place_bet(
                    market=market,
                    selection=selection,
                    amount=amount,
                    reference_sequence=reference_sequence,
                )
            except Exception as exc:  # noqa: BLE001
                return _err(f"bet submission rejected: {exc}")
            if isinstance(result, PendingApproval):
                return _ok(
                    f"bet pending approval: approval_id={result.approval_id}, "
                    f"summary='{result.summary}', "
                    f"expires_at={result.expires_at.isoformat()}. "
                    f"Call check_pending_bet(approval_id) to resolve."
                )
            assert isinstance(result, BetResult)
            return _ok(
                f"bet placed: market={market}, selection={selection}, "
                f"amount={amount}, bet_id={result.bet_id}"
            )

        return place_bet

    async def place_bet(
        market: str,
        selection: str,
        amount: float,
        reference_sequence: int,
    ) -> ToolResponse:
        """Place a bet on the trial's broker.

        Args:
            market: Market name (e.g. ``"moneyline"``, ``"spread"``,
                ``"total"``).
            selection: Side picked. ``"home"`` / ``"away"`` for moneyline
                and spread; ``"over"`` / ``"under"`` for total.
            amount: Stake size in trial currency.
            reference_sequence: Sequence of the odds snapshot the
                decision was made against. The broker uses this to reject
                stale bets.
        """
        try:
            bet = await connection.place_bet(
                market=market,
                selection=selection,
                amount=amount,
                reference_sequence=reference_sequence,
            )
        except Exception as exc:  # noqa: BLE001
            return _err(f"bet rejected: {exc}")
        return _ok(
            f"bet placed: market={market}, selection={selection}, "
            f"amount={amount}, bet_id={getattr(bet, 'bet_id', 'n/a')}, "
            f"new_balance={getattr(bet, 'balance', 'n/a')}"
        )

    return place_bet


def _make_check_pending_bet(connection: "TrialConnection"):
    async def check_pending_bet(approval_id: str) -> ToolResponse:
        """Poll a pending bet approval (spec §7.6.7).

        Use this only after ``place_bet`` returned an ``approval_id``.
        Possible outcomes:

        - ``pending``: keep waiting; the principal hasn't decided.
          Continue with other actions and check back later.
        - ``approved``: the bet was placed (the gateway consumes the
          grant atomically). The response includes the placed
          ``bet_id`` — same shape as a non-approval-mode placement.
        - ``denied``: the principal rejected. May include a note.
          Don't resubmit the same bet without changing it.
        - ``expired``: the principal didn't decide in time. Resubmit
          if you still want the action.

        Args:
            approval_id: The id returned by ``place_bet``.
        """
        try:
            result = await connection.check_pending_bet(approval_id)
        except Exception as exc:  # noqa: BLE001
            return _err(f"check failed: {exc}")
        if isinstance(result, BetResult):
            return _ok(
                f"approved and placed: bet_id={result.bet_id}, "
                f"market={result.market}, selection={result.selection}, "
                f"amount={result.amount}"
            )
        assert isinstance(result, PendingBetStatus)
        if result.status == "pending":
            return _ok(
                f"still pending: approval_id={result.approval_id}, "
                f"expires_at={result.expires_at.isoformat()}"
            )
        if result.status == "denied":
            note = result.denial_note or "(no note)"
            decided_by = result.decided_by or "principal"
            return _ok(
                f"denied by {decided_by}: approval_id={result.approval_id}, "
                f"note='{note}'"
            )
        # "expired"
        return _ok(
            f"expired: approval_id={result.approval_id}, "
            f"expires_at={result.expires_at.isoformat()}"
        )

    return check_pending_bet


__all__ = ["build_tools"]
