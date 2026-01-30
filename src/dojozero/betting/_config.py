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
            "Available: get_balance, get_holdings, place_bet_moneyline, place_bet_spread, "
            "place_bet_total, cancel_bet, get_pending_orders, "
            "get_bet_history, get_statistics, get_event"
        ),
    )


__all__ = [
    "TrialBrokerConfig",
]
