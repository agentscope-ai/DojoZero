"""NBA data store implementation."""

from typing import Any, Sequence

from agentx.data._models import DataEvent
from agentx.data._stores import DataStore, ExternalAPI
from agentx.data.nba._api import NBAExternalAPI
from agentx.data.nba._events import (
    GameResultEvent,
    GameStartEvent,
    GameUpdateEvent,
    InGameCriticalEvent,
    PlayByPlayEvent,
)


class NBAStore(DataStore):
    """NBA data store for polling NBA API and emitting events."""
    
    def __init__(
        self,
        store_id: str = "nba_store",
        api: ExternalAPI | None = None,
        poll_intervals: dict[str, float] | None = None,
        event_emitter=None,
    ):
        """Initialize NBA store.
        
        Default polling intervals:
        - scoreboard: 60.0 seconds (for game updates)
        - play_by_play: 20.0 seconds (for critical in-game events)
        """
        # Set default poll_intervals if not provided
        if poll_intervals is None:
            poll_intervals = {
                "scoreboard": 60.0,
                "play_by_play": 20.0,
            }
        
        super().__init__(
            store_id,
            api or NBAExternalAPI(),
            poll_intervals,
            event_emitter,
        )
        # Track previous game status to detect transitions
        self._previous_game_status: dict[str, int] = {}  # game_id -> gameStatus
    
    def _parse_api_response(self, data: dict[str, Any]) -> Sequence[DataEvent]:
        """Parse NBA API response into DataEvents."""
        from datetime import datetime, timezone
        
        events = []
        
        # Handle scoreboard updates (game_update events)
        # Note: game_start and game_result events are detected from scoreboard data
        # by tracking game_status transitions (see lines 80-106 below)
        if "scoreboard" in data:
            scoreboard_games = data["scoreboard"]
            # Ensure scoreboard_games is a list
            if not isinstance(scoreboard_games, list):
                scoreboard_games = [scoreboard_games] if scoreboard_games else []
            for game_data in scoreboard_games:
                # Ensure game_data is a dict
                if not isinstance(game_data, dict):
                    continue
                timestamp = datetime.now(timezone.utc)
                game_id = game_data.get("gameId", "")
                current_status = game_data.get("gameStatus", 0)
                
                # Extract home and away team data
                # Ensure they are dictionaries (API might return integers in some cases)
                home_team_raw = game_data.get("homeTeam", {})
                away_team_raw = game_data.get("awayTeam", {})
                home_team_data = home_team_raw if isinstance(home_team_raw, dict) else {}
                away_team_data = away_team_raw if isinstance(away_team_raw, dict) else {}
                
                # Check for status transitions
                previous_status = self._previous_game_status.get(game_id)
                
                # Handle status transitions and first-time observations
                if previous_status is None:
                    # First time seeing this game - handle current state
                    if current_status == 2:
                        # Game is already in progress - emit GameStartEvent
                        # (we missed the actual transition, but game has started)
                        events.append(
                            GameStartEvent(
                                timestamp=timestamp,
                                event_id=game_id,
                            )
                        )
                    elif current_status == 3:
                        # Game is already finished - emit GameResultEvent
                        # (we missed the actual transition, but game has ended)
                        home_score = home_team_data.get("score", 0) if isinstance(home_team_data, dict) else 0
                        away_score = away_team_data.get("score", 0) if isinstance(away_team_data, dict) else 0
                        winner = "home" if home_score > away_score else "away" if away_score > home_score else ""
                        
                        events.append(
                            GameResultEvent(
                                timestamp=timestamp,
                                event_id=game_id,
                                winner=winner,
                                final_score={"home": home_score, "away": away_score},
                            )
                        )
                    # If current_status == 1 (Not Started) or 0 (invalid), don't emit anything
                else:
                    # We have previous status - detect transitions
                    # Emit GameStartEvent when status transitions from 1 (Not Started) to 2 (In Progress)
                    if previous_status == 1 and current_status == 2:
                        events.append(
                            GameStartEvent(
                                timestamp=timestamp,
                                event_id=game_id,
                            )
                        )
                    
                    # Emit GameResultEvent when status transitions from 2 (In Progress) to 3 (Finished)
                    if previous_status == 2 and current_status == 3:
                        # Determine winner from scores
                        home_score = home_team_data.get("score", 0) if isinstance(home_team_data, dict) else 0
                        away_score = away_team_data.get("score", 0) if isinstance(away_team_data, dict) else 0
                        winner = "home" if home_score > away_score else "away" if away_score > home_score else ""
                        
                        events.append(
                            GameResultEvent(
                                timestamp=timestamp,
                                event_id=game_id,
                                winner=winner,
                                final_score={"home": home_score, "away": away_score},
                            )
                        )
                
                # Extract game leaders if available
                game_leaders_data = game_data.get("gameLeaders", {})
                game_leaders = {}
                if game_leaders_data and isinstance(game_leaders_data, dict):
                    # Extract home and away team leaders
                    home_leaders_raw = game_leaders_data.get("homeLeaders", {})
                    away_leaders_raw = game_leaders_data.get("awayLeaders", {})
                    home_leaders = home_leaders_raw if isinstance(home_leaders_raw, dict) else {}
                    away_leaders = away_leaders_raw if isinstance(away_leaders_raw, dict) else {}
                    
                    # Helper function to safely extract leader data
                    def extract_leader_data(leaders_dict: dict, stat_type: str) -> dict:
                        """Safely extract leader data for a specific stat type."""
                        if not isinstance(leaders_dict, dict):
                            return {}
                        stat_data = leaders_dict.get(stat_type, {})
                        if not isinstance(stat_data, dict):
                            return {}
                        return {
                            "personId": stat_data.get("personId", 0),
                            "name": stat_data.get("name", ""),
                            "playerSlug": stat_data.get("playerSlug", ""),
                            "jerseyNum": stat_data.get("jerseyNum", ""),
                            "position": stat_data.get("position", ""),
                            "teamTricode": stat_data.get("teamTricode", ""),
                            stat_type: stat_data.get("value", 0),
                        }
                    
                    game_leaders = {
                        "home": {
                            "points": extract_leader_data(home_leaders, "points"),
                            "rebounds": extract_leader_data(home_leaders, "rebounds"),
                            "assists": extract_leader_data(home_leaders, "assists"),
                        },
                        "away": {
                            "points": extract_leader_data(away_leaders, "points"),
                            "rebounds": extract_leader_data(away_leaders, "rebounds"),
                            "assists": extract_leader_data(away_leaders, "assists"),
                        },
                    }
                
                # Always emit GameUpdateEvent with current scoreboard snapshot
                events.append(
                    GameUpdateEvent(
                        timestamp=timestamp,
                        event_id=game_id,  # Use game_id as event_id
                        game_id=game_id,
                        game_status=current_status,
                        game_status_text=game_data.get("gameStatusText", ""),
                        period=game_data.get("period", 0),
                        game_clock=game_data.get("gameClock", ""),
                        game_time_utc=game_data.get("gameTimeUTC", ""),
                        home_team={
                            "teamId": home_team_data.get("teamId", 0),
                            "teamName": home_team_data.get("teamName", ""),
                            "teamCity": home_team_data.get("teamCity", ""),
                            "teamTricode": home_team_data.get("teamTricode", ""),
                            "score": home_team_data.get("score", 0),
                            "wins": home_team_data.get("wins", 0),
                            "losses": home_team_data.get("losses", 0),
                            "seed": home_team_data.get("seed", 0),
                            "timeoutsRemaining": home_team_data.get("timeoutsRemaining", 0),
                            "inBonus": home_team_data.get("inBonus", False),
                            "periods": home_team_data.get("periods", []),  # Quarter-by-quarter scores
                        },
                        away_team={
                            "teamId": away_team_data.get("teamId", 0),
                            "teamName": away_team_data.get("teamName", ""),
                            "teamCity": away_team_data.get("teamCity", ""),
                            "teamTricode": away_team_data.get("teamTricode", ""),
                            "score": away_team_data.get("score", 0),
                            "wins": away_team_data.get("wins", 0),
                            "losses": away_team_data.get("losses", 0),
                            "seed": away_team_data.get("seed", 0),
                            "timeoutsRemaining": away_team_data.get("timeoutsRemaining", 0),
                            "inBonus": away_team_data.get("inBonus", False),
                            "periods": away_team_data.get("periods", []),  # Quarter-by-quarter scores
                        },
                        game_leaders=game_leaders,
                    )
                )
                
                # Update previous status for next poll
                self._previous_game_status[game_id] = current_status
        
        # Handle play-by-play events (from NBA API PlayByPlay endpoint)
        if "play_by_play" in data:
            play_by_play_data = data["play_by_play"]
            game_id = play_by_play_data.get("gameId", "")
            actions = play_by_play_data.get("actions", [])
            
            for action in actions:
                # Parse timestamp from action
                # NBA API actions have actionNumber, period, clock, etc.
                # Try to parse timeActual if available, otherwise use current time
                timestamp = datetime.now(timezone.utc)
                time_actual = action.get("timeActual")
                if time_actual:
                    try:
                        timestamp = datetime.fromisoformat(time_actual.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        pass  # Use default timestamp
                
                # Extract action data
                action_type = action.get("actionType", "")  # String: "rebound", "shot", "foul", etc.
                action_number = action.get("actionNumber", 0)
                period = action.get("period", 0)
                clock = action.get("clock", "")
                person_id = action.get("personId", 0)
                player_name = action.get("playerName", "") or action.get("name", "")
                team_tricode = action.get("teamTricode", "")
                # Handle scoreHome/scoreAway as strings or ints
                home_score = int(action.get("scoreHome", 0) or 0)
                away_score = int(action.get("scoreAway", 0) or 0)
                description = action.get("description", "")
                
                pbp_event = PlayByPlayEvent(
                    timestamp=timestamp,
                    game_id=game_id,
                    action_type=action_type,
                    action_number=action_number,
                    period=period,
                    clock=clock,
                    person_id=person_id,
                    player_name=player_name,
                    team_tricode=team_tricode,
                    home_score=home_score,
                    away_score=away_score,
                    description=description,
                )
                
                # Only emit critical events
                if pbp_event.is_critical():
                    # Also emit InGameCriticalEvent with the specific critical type
                    critical_type = pbp_event.get_critical_type()
                    if critical_type:
                        critical_event_id = f"{game_id}_critical_{action_number}"
                        events.append(
                            InGameCriticalEvent(
                                timestamp=timestamp,
                                event_id=critical_event_id,
                                game_id=game_id,
                                critical_type=critical_type,
                                period=period,
                                clock=clock,
                                player_id=person_id,
                                player_name=player_name,
                                team_tricode=team_tricode,
                                description=description,
                                action_type=action_type,
                                action_number=action_number,
                            )
                        )
        
        return events
    
    async def _poll_api(
        self,
        event_type: str | None = None,
        identifier: dict[str, Any] | None = None,
    ) -> Sequence[DataEvent]:
        """Poll the API for game status, scoreboard updates, and play-by-play events."""
        if not self._api:
            return []
        
        events = []
        
        # Poll scoreboard (for game_update events with full scoreboard snapshot)
        # Check if enough time has passed since last poll
        if self._should_poll_endpoint("scoreboard"):
            scoreboard_params: dict[str, Any] = {}
            if identifier and "game_id" in identifier:
                scoreboard_params["game_id"] = identifier["game_id"]
            if identifier and "game_date" in identifier:
                scoreboard_params["game_date"] = identifier["game_date"]
            
            scoreboard_data = await self._api.fetch("scoreboard", scoreboard_params if scoreboard_params else None)
            if scoreboard_data:
                scoreboard_events = self._parse_api_response(scoreboard_data)
                events.extend(scoreboard_events)
                self._record_poll_time("scoreboard")
        
        # Note: game_start and game_result events are detected from scoreboard data
        # by tracking game_status transitions in _parse_api_response, so we don't need
        # a separate game_status endpoint call.
        
        # Poll play-by-play events (only for live games)
        # Check if enough time has passed since last poll
        if identifier and "game_id" in identifier:
            if self._should_poll_endpoint("play_by_play"):
                game_id = identifier["game_id"]
                pbp_params = {"game_id": game_id}
                
                # Fetch play-by-play data
                pbp_data = await self._api.fetch("play_by_play", pbp_params)
                if pbp_data:
                    pbp_events = self._parse_api_response(pbp_data)
                    events.extend(pbp_events)
                    self._record_poll_time("play_by_play")
        
        return events

