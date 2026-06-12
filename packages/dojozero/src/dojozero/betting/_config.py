"""Shared configuration models for betting trial params.

These Pydantic models are used in trial builder params (YAML) to configure
betting operators. They are distinct from actor configs (TypedDicts in
_broker.py / _prediction_broker.py) which are used for actor instantiation.

Hierarchy:
- Trial params YAML -> TrialBrokerConfig (Pydantic, validated at build time)
- Trial builder -> converts to BrokerOperatorConfig or PredictionBrokerConfig
- Actor.from_dict() -> receives the appropriate TypedDict
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TrialBrokerConfig(BaseModel):
    """Configuration for broker-style operators in trial params.

    Supports two operator classes:

    - ``BrokerOperator``: classic betting (accounts, bets, odds).
    - ``PredictionBroker``: prediction contest with five fixed windows.
      Uses the ``window_pools`` field for prize pools per window.

    Note: This is distinct from the per-class ``*Config`` TypedDicts used
    for actor instantiation.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="Unique identifier for this operator")
    class_name: str = Field(
        alias="class",
        description="Operator class name: 'BrokerOperator' or 'PredictionBroker'",
    )
    data_streams: list[str] = Field(
        default_factory=list, description="DataStream actor IDs to subscribe to"
    )
    initial_balance: str | None = Field(
        default=None,
        description="Initial balance (BrokerOperator only)",
    )
    allowed_tools: list[str] | None = Field(
        default=None,
        description=(
            "List of allowed agent tool names (default: all tools). "
            "BrokerOperator: get_balance, get_holdings, get_event, "
            "place_market_bet_moneyline, place_limit_bet_moneyline, "
            "place_market_bet_spread, place_limit_bet_spread, "
            "place_market_bet_total, place_limit_bet_total, "
            "cancel_bet, get_pending_orders, get_bet_history, get_statistics. "
            "PredictionBroker: get_rules, get_event_info, submit_prediction, "
            "get_my_predictions."
        ),
    )

    # --- PredictionBroker fields ---
    window_pools: list[int] | None = Field(
        default=None,
        description=(
            "Prize pool per contest window for PredictionBroker. Must have "
            "exactly 5 entries: [pre-game, Q1, Q2, Q3, Q4]."
        ),
    )

    @model_validator(mode="after")
    def _validate_window_pools_length(self) -> "TrialBrokerConfig":
        if self.window_pools is not None and len(self.window_pools) != 5:
            raise ValueError(
                f"window_pools must have exactly 5 entries, got {len(self.window_pools)}"
            )
        return self

    @model_validator(mode="after")
    def _validate_window_pools_class(self) -> "TrialBrokerConfig":
        """Reject ``window_pools`` on a non-PredictionBroker config.

        Catches a common mistake at trial-build time instead of letting the
        field silently no-op when the operator class is ``BrokerOperator``.
        """
        if self.window_pools is not None and self.class_name != "PredictionBroker":
            raise ValueError(
                f"window_pools is only valid for PredictionBroker, "
                f"got class_name={self.class_name!r}"
            )
        return self


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

# Prompt for LLM to generate hot topics from recent social board messages (every N posts)
HOT_TOPICS_PROMPT = """Based on the following recent messages from a multi-agent social board (sports betting context), output a short "hot topics" or "trending discussion" list. Extract 3–5 main themes or discussion points that agents are focusing on. Output only a numbered list, one topic per line, e.g.:
1. First topic summary
2. Second topic summary
Do not add any other text or explanation.

Recent messages:
---
{recent_messages}
---
"""

__all__ = ["TrialBrokerConfig", "MEMORY_SUMMARY_PROMPT", "HOT_TOPICS_PROMPT"]
