"""Agentscope tool functions backed by ``dojozero-client``.

Each builder takes a live ``TrialConnection`` and returns an async function
shaped for ``Toolkit.register_tool_function``. The agent calls them through
the ReAct loop; the LLM never touches the SDK directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

if TYPE_CHECKING:
    from dojozero_client import TrialConnection


def build_tools(connection: "TrialConnection") -> list:
    """Return a list of agentscope-compatible tool callables for ``connection``.

    Each callable closes over the live connection so the LLM only needs to
    pass the action-shaped kwargs (market, selection, amount, etc.). The
    tools are intentionally narrow — the canary only needs the actions a
    persona uses to react to a live game.
    """
    return [
        _make_get_balance(connection),
        _make_get_current_odds(connection),
        _make_place_bet(connection),
    ]


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


def _make_place_bet(connection: "TrialConnection"):
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


__all__ = ["build_tools"]
