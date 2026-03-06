"""Shared configuration models for betting trial params.

These Pydantic models are used in trial builder params (YAML) to configure
betting operators. They are distinct from actor configs (TypedDicts in
_broker.py) which are used for actor instantiation.

Hierarchy:
- Trial params YAML -> TrialBrokerConfig (Pydantic, validated at build time)
- Trial builder -> converts to BrokerOperatorConfig (TypedDict)
- Actor.from_dict() -> receives BrokerOperatorConfig
"""

from pydantic import BaseModel, ConfigDict, Field


class TrialBrokerConfig(BaseModel):
    """Configuration for broker operators in trial params.

    Used in the `operators:` list in trial params YAML to define
    broker operator settings.

    Note: This is distinct from BrokerOperatorConfig in _broker.py
    which is the TypedDict used for actor instantiation.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="Unique identifier for this operator")
    class_name: str = Field(alias="class", description="Operator class name")
    data_streams: list[str] = Field(
        default_factory=list, description="DataStream actor IDs to subscribe to"
    )
    initial_balance: str | None = Field(
        default=None, description="Initial balance for broker (if applicable)"
    )
    allowed_tools: list[str] | None = Field(
        default=None,
        description=(
            "List of allowed agent tool names (default: all tools). "
            "Available: get_balance, get_holdings, get_event, "
            "place_market_bet_moneyline, place_limit_bet_moneyline, "
            "place_market_bet_spread, place_limit_bet_spread, "
            "place_market_bet_total, place_limit_bet_total, "
            "cancel_bet, get_pending_orders, get_bet_history, get_statistics"
        ),
    )


MEMORY_SUMMARY_PROMPT = """\
You are a memory compressor for a sports forecasting AI. Summarize the conversation below into a concise context block under 1500 tokens.

Include ONLY sections with relevant content:

[Pre-Game Analysis]
- Injuries, lineup, form, rest/schedule, line movement, key matchups

[Game Progress]
- Score, period/time, momentum shifts, foul trouble/absences

[Betting Record]
- Selection | Amount | Probability | Outcome | Reasoning

[Market Context]
- Latest probabilities & notable odds movements

Rules:
- Never omit numbers (scores, odds, amounts).
- Never invent facts not in the conversation.
- Keep total output under 1500 tokens.
"""

__all__ = ["TrialBrokerConfig", "MEMORY_SUMMARY_PROMPT"]
