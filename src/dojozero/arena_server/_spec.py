"""
Frontend API Contract Definitions - WebSocket Protocol Only.

Payload types are NOT defined here. They use the canonical models directly:
- data/_models.py: Game events, value objects (TeamIdentity, OddsInfo, etc.)
- data/nba/_events.py: NBAPlayEvent, NBAGameUpdateEvent
- data/nfl/_events.py: NFLPlayEvent, NFLDriveEvent, NFLGameUpdateEvent
- betting/_models.py: Bet, Account, AgentResponseMessage, CoTStep

The category is derived from the event's `event_type` field.
Serialization uses `model_dump(by_alias=True)` for camelCase output.
"""

from __future__ import annotations


# =============================================================================
# WebSocket Message Categories (operation_name patterns)
# =============================================================================


class WSCategory:
    """WebSocket message category constants.

    These match the event_type / operation_name from spans and are used
    by frontend to dispatch incoming messages to the appropriate handlers.
    """

    # Event categories (game state) - from data/_models.py
    GAME_INITIALIZE = "event.game_initialize"
    GAME_START = "event.game_start"
    GAME_RESULT = "event.game_result"
    NBA_PLAY = "event.nba_play"
    NFL_PLAY = "event.nfl_play"
    NFL_DRIVE = "event.nfl_drive"
    NBA_GAME_UPDATE = "event.nba_game_update"
    NFL_GAME_UPDATE = "event.nfl_game_update"
    ODDS_UPDATE = "event.odds_update"

    # Pre-game insight categories
    INJURY_REPORT = "event.injury_report"
    POWER_RANKING = "event.power_ranking"
    EXPERT_PREDICTION = "event.expert_prediction"
    PREGAME_STATS = "event.pregame_stats"

    # Agent categories
    AGENT_RESPONSE = "agent.response"
    AGENT_REGISTERED = "agent.registered"

    # Broker categories - use betting/_models.py types
    BROKER_BET = "broker.bet"
    BROKER_STATE_UPDATE = "broker.state_update"

    # Trial lifecycle
    TRIAL_STARTED = "trial.started"
    TRIAL_STOPPED = "trial.stopped"
    TRIAL_TERMINATED = "trial.terminated"


# =============================================================================
# WebSocket Message Types
# =============================================================================


class WSMessageType:
    """WebSocket message type constants."""

    SNAPSHOT = "snapshot"
    SPAN = "span"
    TRIAL_ENDED = "trial_ended"
    HEARTBEAT = "heartbeat"


__all__ = [
    "WSCategory",
    "WSMessageType",
]
