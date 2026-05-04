"""Smoke tests for the runner core.

Full end-to-end coverage needs a live gateway + AgentID provider, so these
tests focus on what we can verify in isolation: module imports cleanly,
the event formatter handles the shapes we'll see, and the tool builders
produce the expected callable surface.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dojozero_agent_runner._runner import _format_event
from dojozero_agent_runner._tools import build_tools


class TestFormatEvent:
    def test_minimal_event(self):
        event = SimpleNamespace(event_type="game_start", sequence=1, payload=None)
        assert _format_event(event) == "event_type=game_start | sequence=1"

    def test_event_with_payload(self):
        event = SimpleNamespace(
            event_type="play_by_play",
            sequence=42,
            payload={"team": "home", "score": 7},
        )
        text = _format_event(event)
        assert "event_type=play_by_play" in text
        assert "sequence=42" in text
        assert "payload=" in text
        assert "team" in text and "home" in text

    def test_unknown_event_shape_does_not_raise(self):
        """Defensive shape — missing attrs should fall through to defaults."""
        text = _format_event(SimpleNamespace())
        assert "event_type=unknown" in text
        assert "sequence=?" in text


class TestBuildTools:
    def test_returns_three_tools(self):
        connection = AsyncMock()
        tools = build_tools(connection)
        names = [t.__name__ for t in tools]
        assert names == ["get_balance", "get_current_odds", "place_bet"]

    @pytest.mark.asyncio
    async def test_get_balance_calls_connection(self):
        connection = AsyncMock()
        connection.get_balance = AsyncMock(
            return_value=SimpleNamespace(balance="1000.00")
        )
        connection.last_sequence = 42

        tools = build_tools(connection)
        get_balance = tools[0]
        response = await get_balance()

        text = response.content[0]["text"]
        assert "balance=1000.00" in text
        assert "last_sequence=42" in text
        connection.get_balance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_place_bet_passes_args_through(self):
        connection = AsyncMock()
        connection.place_bet = AsyncMock(
            return_value=SimpleNamespace(bet_id="bet_42", balance="900")
        )

        tools = build_tools(connection)
        place_bet = tools[2]
        response = await place_bet(
            market="moneyline",
            selection="home",
            amount=100.0,
            reference_sequence=17,
        )

        connection.place_bet.assert_awaited_once_with(
            market="moneyline",
            selection="home",
            amount=100.0,
            reference_sequence=17,
        )
        text = response.content[0]["text"]
        assert "bet placed" in text
        assert "bet_id=bet_42" in text

    @pytest.mark.asyncio
    async def test_place_bet_returns_error_on_exception(self):
        connection = AsyncMock()
        connection.place_bet = AsyncMock(side_effect=RuntimeError("BETTING_CLOSED"))

        tools = build_tools(connection)
        place_bet = tools[2]
        response = await place_bet(
            market="moneyline", selection="home", amount=10.0, reference_sequence=1
        )

        text = response.content[0]["text"]
        assert "ERROR" in text
        assert "BETTING_CLOSED" in text
