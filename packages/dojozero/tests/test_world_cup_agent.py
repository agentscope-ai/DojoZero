"""Tests for World Cup betting agent construction."""

from __future__ import annotations

from typing import Any

import dojozero.agents as agents_module
from dojozero.core import RuntimeContext
from dojozero.world_cup._agent import BettingAgent


def test_from_dict_appends_agent_identity(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_init(self, *args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(BettingAgent, "__init__", fake_init)
    monkeypatch.setattr(agents_module, "create_model", lambda config: object())
    monkeypatch.setattr(
        agents_module,
        "create_formatter",
        lambda model_type, model_name: object(),
    )

    BettingAgent.from_dict(
        {
            "actor_id": "wc_agent",
            "name": "World Cup Agent",
            "sys_prompt": "Play carefully.",
            "llm": {"model_type": "openai", "model_name": "gpt-test"},
        },
        RuntimeContext(trial_id="trial-1", sport_type="world_cup"),
    )

    assert captured["sys_prompt"] == (
        "Play carefully.\n\n[Agent Identity]\nYour agent id is wc_agent."
    )
