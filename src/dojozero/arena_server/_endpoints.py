"""Arena Server Endpoints.

This module contains all REST and WebSocket endpoint handlers for the Arena Server.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dojozero.arena_server._models import (
    AgentActionsResponse,
    GameCardData,
    LandingResponse,
    LeaderboardResponse,
    ReplayResponse,
    TrialDetailResponse,
    TrialListItem,
    WSHeartbeatMessage,
    WSReplayMetaInfoMessage,
    WSReplayUnavailableMessage,
    WSSnapshotMessage,
    WSSpanMessage,
    WSStreamStatusMessage,
    WSTrialEndedMessage,
    ReplayErrorReason,
)

from ._server import (
    CategoryFilter,
    StreamController,
    TrialReplayController,
    get_server_state,
)
from ._utils import _load_replay_data

LOGGER = logging.getLogger("dojozero.arena_server.endpoints")


# =============================================================================
# REST Endpoints
# =============================================================================


def register_rest_endpoints(app: FastAPI) -> None:
    """Register all REST API endpoints."""

    @app.get("/api/trials")
    async def list_trials(
        start_time: int | None = Query(
            default=None,
            description="Start time as Unix timestamp (seconds). Defaults to 7 days ago.",
        ),
        end_time: int | None = Query(
            default=None,
            description="End time as Unix timestamp (seconds). Defaults to now.",
        ),
        limit: int = Query(
            default=500,
            description="Maximum number of trials to return.",
            ge=1,
            le=1000,
        ),
    ) -> JSONResponse:
        """List trials with metadata extracted from traces.

        Query Parameters:
            start_time: Start time as Unix timestamp in seconds (default: 7 days ago)
            end_time: End time as Unix timestamp in seconds (default: now)
            limit: Maximum number of trials to return (default: 500, max: 1000)
        """
        from ._utils import _extract_trial_info_from_traces

        state = get_server_state()

        # Convert Unix timestamps to datetime
        start_dt = (
            datetime.fromtimestamp(start_time, tz=timezone.utc)
            if start_time is not None
            else None
        )
        end_dt = (
            datetime.fromtimestamp(end_time, tz=timezone.utc)
            if end_time is not None
            else None
        )

        try:
            # Get trial list from trace store with time range filter
            trial_ids = await state.trace_reader.list_trials(
                start_time=start_dt,
                end_time=end_dt,
                limit=limit,
            )

            # Build result with phase and metadata extracted from traces
            result: list[TrialListItem] = []
            for tid in trial_ids:
                if not tid or tid == "None":
                    continue
                trial_info_extracted = await _extract_trial_info_from_traces(
                    state.trace_reader, tid
                )
                result.append(
                    TrialListItem(
                        id=tid,
                        phase=trial_info_extracted["phase"],
                        metadata=trial_info_extracted.get("metadata", {}),
                    )
                )

            # jsonable_encoder tolerates odd types in metadata better than model_dump(mode="json")
            payload = jsonable_encoder(
                [item.model_dump(by_alias=state.by_alias) for item in result]
            )
        except Exception:
            LOGGER.exception("GET /api/trials failed")
            return JSONResponse(
                status_code=503,
                content={"detail": "trial_list_failed"},
            )

        return JSONResponse(content=payload)

    @app.get("/api/trials/{trial_id}")
    async def get_trial(trial_id: str) -> JSONResponse:
        """Get trial info and spans.

        Data is served from cache (background refresh keeps it fresh for live trials).
        On cache miss, triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        start_time_ts = time.time()
        LOGGER.info("Fetching trial details for: %s", trial_id)

        # Get trial details from cache
        cached = state.cache.get_trial_details(trial_id)
        if cached is not None:
            items = cached.get("items", [])
        else:
            # Cache miss - refresh on demand
            items = await refresher.refresh_trial_details_on_demand(trial_id)

        elapsed = time.time() - start_time_ts
        LOGGER.info(
            "Trial %s: Returned %d items in %.2fs",
            trial_id,
            len(items) if items else 0,
            elapsed,
        )

        if not items:
            # Check if trial exists (may have no spans yet)
            trial_ids = state.cache.get_trials_list() or []
            if trial_id not in trial_ids:
                return JSONResponse(
                    content={"error": f"Trial '{trial_id}' not found"},
                    status_code=404,
                )

        response = TrialDetailResponse(trial_id=trial_id, items=items)
        return JSONResponse(
            content=response.model_dump(
                by_alias=state.by_alias,
                exclude={
                    "live_games": {
                        "__all__": {
                            "home_team": {"players"},
                            "away_team": {"players"},
                        }
                    },
                    "all_games": {
                        "__all__": {
                            "home_team": {"players"},
                            "away_team": {"players"},
                        }
                    },
                },
            )
        )

    @app.get("/api/landing")
    async def get_landing_data(
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc. Returns all if not specified.",
        ),
    ) -> JSONResponse:
        """Get aggregated landing page data.

        Returns games, stats, and recent agent actions in a single call.
        Data is served from cache (background refresh keeps it fresh).

        On cache miss (new data not yet in cache), triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        # Get stats from cache, or refresh on demand
        stats = state.cache.get_stats(league=league)
        if stats is None:
            stats = await refresher.refresh_stats_on_demand(league=league)

        # Get games from cache, or refresh on demand
        games = state.cache.get_games(league=league)
        if games is None:
            games = await refresher.refresh_games_on_demand(league=league)

        # Get agent actions from cache, or refresh on demand
        agent_actions = state.cache.get_agent_actions(league=league)
        if agent_actions is None:
            agent_actions = await refresher.refresh_agent_actions_on_demand(
                league=league
            )

        all_games = games.live_games + games.upcoming_games + games.completed_games
        live_games = games.live_games

        if league and league.upper() == "NFL":
            from dojozero.arena_server._constants import SUPER_BOWL_GAME_ID

            superbowl_game = next(
                (g for g in all_games if SUPER_BOWL_GAME_ID in g.id), None
            )
            if superbowl_game and superbowl_game not in live_games:
                live_games = [superbowl_game] + list(live_games)

        # only for mock test
        if league and league.upper() == "NBA" and not live_games:
            live_games = list(all_games[:10])

        response = LandingResponse(
            stats=stats,
            live_games=live_games,
            all_games=all_games,
            live_agent_actions=agent_actions,
        )
        return JSONResponse(
            content=response.model_dump(
                by_alias=state.by_alias,
                exclude={
                    "live_games": {
                        "__all__": {
                            "home_team": {"players"},
                            "away_team": {"players"},
                        }
                    },
                    "all_games": {
                        "__all__": {
                            "home_team": {"players"},
                            "away_team": {"players"},
                        }
                    },
                },
            )
        )

    @app.get("/api/stats")
    async def get_stats(
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc. Returns all if not specified.",
        ),
    ) -> JSONResponse:
        """Get real-time stats for the hero section.

        Returns:
            gamesPlayed: Total completed games
            liveNow: Currently running games
            wageredToday: Total amount wagered (if available)

        Data is served from cache (background refresh keeps it fresh).
        On cache miss, triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        # Get stats from cache, or refresh on demand
        stats = state.cache.get_stats(league=league)
        if stats is None:
            stats = await refresher.refresh_stats_on_demand(league=league)

        return JSONResponse(content=stats.model_dump(by_alias=state.by_alias))

    @app.get("/api/games")
    async def get_games(
        status: str | None = Query(
            default=None,
            description="Filter by status: 'live', 'upcoming', 'completed', or 'all'.",
        ),
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc.",
        ),
        limit: int = Query(
            default=50,
            description="Maximum number of games to return.",
            ge=1,
            le=200,
        ),
    ) -> JSONResponse:
        """Get games list with optional filters.

        Returns games grouped by status or filtered by query params.
        Data is served from cache (background refresh keeps it fresh).
        On cache miss, triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        # Get games from cache, or refresh on demand
        games_data = state.cache.get_games(league=league)
        if games_data is None:
            games_data = await refresher.refresh_games_on_demand(league=league)

        # Apply status filter
        all_games: list[GameCardData] = (
            games_data.live_games
            + games_data.upcoming_games
            + games_data.completed_games
        )

        if status:
            status_map = {
                "live": "running",
                "upcoming": "upcoming",
                "completed": "completed",
            }
            target_status = status_map.get(status, status)
            all_games = [g for g in all_games if g.status == target_status]

        filtered = all_games[:limit]
        return JSONResponse(
            content={
                "games": [g.model_dump(by_alias=state.by_alias) for g in filtered],
                "total": len(all_games),
            }
        )

    @app.get("/api/leaderboard")
    async def get_leaderboard(
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc.",
        ),
        limit: int = Query(
            default=20,
            description="Maximum number of agents to return.",
            ge=1,
            le=100,
        ),
    ) -> JSONResponse:
        """Get agent leaderboard ranked by winnings.

        Returns agents sorted by total winnings with win rate and ROI.
        Data is served from cache (background refresh keeps it fresh).
        On cache miss, triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        # Get leaderboard from cache, or refresh on demand
        leaderboard = state.cache.get_leaderboard(league=league)
        if leaderboard is None:
            leaderboard = await refresher.refresh_leaderboard_on_demand(league=league)

        # Apply limit
        leaderboard = leaderboard[:limit]

        response = LeaderboardResponse(leaderboard=leaderboard)
        return JSONResponse(
            content=response.model_dump(by_alias=state.by_alias),
        )

    @app.get("/api/agent-actions")
    async def get_agent_actions(
        limit: int = Query(
            default=20,
            description="Maximum number of actions to return.",
            ge=1,
            le=100,
        ),
        league: str | None = Query(
            default=None,
            description="Filter by league: 'NBA', 'NFL', etc. Returns all if not specified.",
        ),
    ) -> JSONResponse:
        """Get recent agent actions for the live ticker.

        Returns the most recent agent actions sorted by time.
        Data is served from cache (background refresh keeps it fresh).
        On cache miss, triggers on-demand refresh.
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        # Get agent actions from cache, or refresh on demand
        actions = state.cache.get_agent_actions(league=league)
        if actions is None:
            actions = await refresher.refresh_agent_actions_on_demand(league=league)

        # Apply limit
        actions = actions[:limit]

        response = AgentActionsResponse(actions=actions)
        return JSONResponse(
            content=response.model_dump(by_alias=state.by_alias),
        )

    @app.post("/api/trials/{trial_id}/replay")
    async def get_trial_replay_data(
        trial_id: str,
        categories: str | None = Query(
            default=None,
            description="Comma-separated categories to filter (e.g., 'play,game_update'). "
            "If not specified, all categories are included.",
        ),
        filter_mode: str = Query(
            default="include",
            description="Filter mode: 'include' (only specified categories) or "
            "'exclude' (all except specified categories).",
        ),
    ) -> JSONResponse:
        """Get all replay data for a completed trial at once.

        This endpoint returns all spans for a completed trial in a single response,
        allowing the frontend to implement its own playback logic.

        Only works for trials that have ended (trial.stopped/terminated).

        Query Parameters:
            categories: Comma-separated list of categories to filter
            filter_mode: 'include' or 'exclude'

        Returns:
            ReplayResponse with:
                - available: True if replay data is available
                - reason: Error reason if not available
                - items: List of serialized spans (filtered by category if specified)
                - totalItems: Total number of items after filtering
        """
        state = get_server_state()
        refresher = state.refresher

        cache_entry, error_reason = await _load_replay_data(
            state.trace_reader,
            state.replay_cache,
            trial_id,
            redis_reader=(
                refresher.redis_reader
                if refresher is not None and refresher._use_redis
                else None
            ),
        )

        if cache_entry is None:
            response = ReplayResponse(
                trial_id=trial_id,
                available=False,
                reason=cast(ReplayErrorReason, error_reason),
                items=[],
                total_items=0,
            )
            # Return 200 with available=false rather than 404
            # This allows frontend to handle gracefully
            return JSONResponse(content=response.model_dump(by_alias=state.by_alias))

        # Apply category filter
        cat_filter = CategoryFilter.from_query(categories, filter_mode)
        filtered_items = cat_filter.filter_items(cache_entry.items)

        response = ReplayResponse(
            trial_id=trial_id,
            available=True,
            items=filtered_items,
            reason=None,
            total_items=len(filtered_items),
        )
        return JSONResponse(content=response.model_dump(by_alias=state.by_alias))

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        state = get_server_state()
        return {
            "status": "ok",
            "static_dir": str(state.static_dir) if state.static_dir else None,
        }

    @app.get("/api/cache-stats")
    async def get_cache_stats() -> JSONResponse:
        """Get cache statistics for debugging and monitoring.

        Returns current cache configuration and status of all cache entries.
        Useful for diagnosing caching issues and tuning TTL values.
        """
        state = get_server_state()
        return JSONResponse(content=state.cache.get_cache_stats())


def register_static_file_serving(app: FastAPI, static_dir: Path) -> None:
    """Register static file serving for SPA (Single Page Application)."""
    if not static_dir.exists():
        return

    # Serve static files
    app.mount(
        "/assets",
        StaticFiles(directory=static_dir / "assets"),
        name="assets",
    )

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        """Serve static files with SPA fallback."""
        file_path = static_dir / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        # SPA fallback
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return JSONResponse(
            content={"error": "Not found"},
            status_code=404,
        )


# =============================================================================
# WebSocket Endpoints
# =============================================================================


def register_websocket_endpoints(app: FastAPI) -> None:
    """Register all WebSocket endpoints."""

    @app.websocket("/ws/trials/{trial_id}/stream")
    async def trial_stream(
        websocket: WebSocket,
        trial_id: str,
        categories: str | None = Query(
            default=None,
            description="Comma-separated categories to filter (e.g., 'play,game_update'). "
            "If not specified, all categories are included.",
        ),
        filter_mode: str = Query(
            default="include",
            description="Filter mode: 'include' or 'exclude'.",
        ),
    ):
        """WebSocket endpoint for real-time span streaming with pause/resume and filtering.

        Uses cached trial_details (refreshed by background task every 5s for live trials).
        On cache miss, triggers on-demand refresh.

        Protocol:
        - Server sends 'snapshot' immediately upon connection
        - Server pushes 'span' messages as new spans are detected in cache
        - Server sends 'trial_ended' when trial completes
        - Server sends 'heartbeat' periodically
        - Server sends 'stream_status' in response to control commands

        Control commands (send as JSON):
            {"command": "pause"}   - Pause streaming (buffers items)
            {"command": "resume"}  - Resume streaming (sends buffered items)
            {"command": "status"}  - Get current stream status
            {"command": "filter", "categories": [...], "mode": "include"}
                                   - Update category filter dynamically
            {"command": "disconnect"} - Close connection gracefully
        """
        state = get_server_state()
        refresher = state.refresher
        assert refresher is not None, "BackgroundRefresher not initialized"

        await websocket.accept()
        LOGGER.info("WebSocket connection accepted for trial '%s'", trial_id)

        # Per-connection stream controller (stores items during pause, not raw spans)
        controller = StreamController()

        # Initialize category filter from query params
        cat_filter = CategoryFilter.from_query(categories, filter_mode)

        try:
            await state.broadcaster.subscribe(trial_id, websocket)

            # Get initial snapshot from cache (or refresh on demand)
            cached = state.cache.get_trial_details(trial_id)
            if cached is None:
                # Cache miss - refresh on demand
                await refresher.refresh_trial_details_on_demand(trial_id)
                cached = state.cache.get_trial_details(trial_id)

            all_items = cached.get("items", []) if cached else []
            is_completed = cached.get("is_completed", False) if cached else False

            # Filter and send snapshot
            snapshot_items = cat_filter.filter_items(all_items)
            snapshot_msg = WSSnapshotMessage(
                trial_id=trial_id,
                timestamp=datetime.now(timezone.utc),
                data={"items": snapshot_items},
            )
            await websocket.send_text(snapshot_msg.model_dump_json())
            LOGGER.info(
                "Stream: Sent snapshot with %d items (filtered from %d) for trial '%s'",
                len(snapshot_items),
                len(all_items),
                trial_id,
            )

            # Track how many items we've sent (to detect new items in cache)
            items_sent_count = len(all_items)

            # If already completed, send trial_ended and close
            if is_completed:
                ended_msg = WSTrialEndedMessage(
                    trial_id=trial_id,
                    timestamp=datetime.now(timezone.utc),
                )
                await websocket.send_text(ended_msg.model_dump_json())
                LOGGER.info("Trial '%s' already completed, closing stream", trial_id)
                return

            # Pause buffer: stores items (not raw spans)
            pause_buffer: list[dict[str, Any]] = []

            # Poll for new items in cache
            while True:
                try:
                    # Wait for client message or timeout
                    msg_text = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=state.poll_interval,
                    )

                    # Handle control commands
                    try:
                        command_data = json.loads(msg_text)
                        command = command_data.get("command", "")

                        if command == "pause":
                            controller.pause()
                            status_msg = WSStreamStatusMessage(
                                is_paused=True,
                                buffer_size=len(pause_buffer),
                                timestamp=datetime.now(timezone.utc),
                            )
                            await websocket.send_text(status_msg.model_dump_json())

                        elif command == "resume":
                            controller.resume()
                            # Send buffered items as snapshot (catch-up mode)
                            if pause_buffer:
                                filtered_buffered = cat_filter.filter_items(
                                    pause_buffer
                                )
                                if filtered_buffered:
                                    snapshot_msg = WSSnapshotMessage(
                                        trial_id=trial_id,
                                        timestamp=datetime.now(timezone.utc),
                                        data={"items": filtered_buffered},
                                    )
                                    await websocket.send_text(
                                        snapshot_msg.model_dump_json()
                                    )
                            buffered_count = len(pause_buffer)
                            pause_buffer = []
                            status_msg = WSStreamStatusMessage(
                                is_paused=False,
                                buffered_count=buffered_count,
                                timestamp=datetime.now(timezone.utc),
                            )
                            await websocket.send_text(status_msg.model_dump_json())

                        elif command == "filter":
                            # Update category filter dynamically
                            filter_categories = command_data.get("categories", [])
                            filter_mode_cmd = command_data.get("mode", "include")
                            cat_filter = CategoryFilter.from_list(
                                filter_categories, filter_mode_cmd
                            )
                            LOGGER.debug(
                                "Stream: Updated filter for trial '%s': categories=%s, mode=%s",
                                trial_id,
                                filter_categories,
                                filter_mode_cmd,
                            )
                            status_msg = WSStreamStatusMessage(
                                is_paused=controller.is_paused,
                                buffer_size=len(pause_buffer),
                                timestamp=datetime.now(timezone.utc),
                            )
                            await websocket.send_text(status_msg.model_dump_json())

                        elif command == "status":
                            status_msg = WSStreamStatusMessage(
                                is_paused=controller.is_paused,
                                buffer_size=len(pause_buffer),
                                timestamp=datetime.now(timezone.utc),
                            )
                            await websocket.send_text(status_msg.model_dump_json())

                        elif command == "disconnect":
                            LOGGER.info(
                                "Stream: Client requested disconnect for trial '%s'",
                                trial_id,
                            )
                            return

                    except json.JSONDecodeError:
                        LOGGER.warning("Invalid JSON command: %s", msg_text)

                except asyncio.TimeoutError:
                    # Check cache for new items
                    cached = state.cache.get_trial_details(trial_id)
                    if cached is None:
                        # Cache was invalidated, skip this cycle
                        pass
                    else:
                        current_items = cached.get("items", [])
                        is_now_completed = cached.get("is_completed", False)

                        # Check for new items
                        if len(current_items) > items_sent_count:
                            new_items = current_items[items_sent_count:]

                            for item in new_items:
                                if controller.is_paused:
                                    # Buffer during pause
                                    if len(pause_buffer) < 1000:  # Max buffer size
                                        pause_buffer.append(item)
                                else:
                                    # Send immediately (with category filter)
                                    if not cat_filter.filter_item(item):
                                        continue

                                    message = WSSpanMessage(
                                        trial_id=trial_id,
                                        timestamp=datetime.now(timezone.utc),
                                        category=item.get("category", ""),
                                        data=item.get("data", {}),
                                    )
                                    await websocket.send_text(message.model_dump_json())

                            items_sent_count = len(current_items)

                            if not controller.is_paused:
                                LOGGER.debug(
                                    "Sent %d new items for trial '%s'",
                                    len(new_items),
                                    trial_id,
                                )

                        # Check if trial just completed
                        if is_now_completed:
                            ended_msg = WSTrialEndedMessage(
                                trial_id=trial_id,
                                timestamp=datetime.now(timezone.utc),
                            )
                            await websocket.send_text(ended_msg.model_dump_json())
                            LOGGER.info(
                                "Trial '%s' completed, closing stream", trial_id
                            )
                            return

                    # Send heartbeat (even when paused, to keep connection alive)
                    heartbeat = WSHeartbeatMessage(
                        timestamp=datetime.now(timezone.utc),
                    )
                    await websocket.send_text(heartbeat.model_dump_json())

        except WebSocketDisconnect:
            LOGGER.info("WebSocket disconnected for trial '%s'", trial_id)
        except Exception as e:
            LOGGER.error("WebSocket error for trial '%s': %s", trial_id, e)
        finally:
            await state.broadcaster.unsubscribe(trial_id, websocket)

    @app.websocket("/ws/trials/{trial_id}/replay")
    async def trial_replay(
        websocket: WebSocket,
        trial_id: str,
        autostart: bool = Query(
            default=True,
            description="Auto-start playback on connection. If false, waits for resume.",
        ),
        snapshot_size: int = Query(
            default=20,
            description="Number of items to include in snapshot.",
            ge=1,
            le=200,
        ),
        categories: str | None = Query(
            default=None,
            description="Comma-separated categories to filter (e.g., 'play,game_update'). "
            "If not specified, all categories are included.",
        ),
        filter_mode: str = Query(
            default="include",
            description="Filter mode: 'include' or 'exclude'.",
        ),
    ):
        """WebSocket endpoint for replaying completed trials.

        Only works for trials that have ended (trial.stopped/terminated).
        Replays at uniform speed with support for pause/resume, speed control, seeking,
        and category filtering.

        Query Parameters:
            autostart: If false, only sends meta_info; waits for resume to start (default: true)
            snapshot_size: Number of items in snapshot (default: 20)
            categories: Comma-separated categories to filter (e.g., 'play,game_update')
            filter_mode: 'include' or 'exclude' (default: 'include')

        Control commands (send as JSON):
            {"command": "pause"}                     - Pause replay
            {"command": "resume"}                    - Resume replay (also starts if autostart=false)
            {"command": "speed", "value": 2}         - Set speed (1, 2, 4, 10, or 20)
            {"command": "reset"}                     - Restart from beginning
            {"command": "seek", "play_index": N}     - Seek to play index N (0-based)
            {"command": "status"}                    - Get current status
            {"command": "filter", "categories": [...], "mode": "include"}
                                                     - Update category filter dynamically
            {"command": "disconnect"}                - Close connection gracefully

        Server messages:
            {"type": "replay_meta_info", ...}       - Metadata (sent first, always)
            {"type": "snapshot", ...}               - Batch of events
            {"type": "span", ...}                   - Single event during playback
            {"type": "replay_status", ...}          - Playback status update
            {"type": "replay_unavailable", ...}     - Replay not available
            {"type": "trial_ended", ...}            - End of replay
            {"type": "heartbeat", ...}              - Keepalive (fixed interval)
        """
        state = get_server_state()
        await websocket.accept()
        LOGGER.info(
            "Replay WebSocket connection accepted for trial '%s' (autostart=%s, snapshot_size=%d)",
            trial_id,
            autostart,
            snapshot_size,
        )

        # Load replay data (includes pre-computed meta)
        refresher = state.refresher
        cache_entry, error_reason = await _load_replay_data(
            state.trace_reader,
            state.replay_cache,
            trial_id,
            redis_reader=(
                refresher.redis_reader
                if refresher is not None and refresher._use_redis
                else None
            ),
        )

        if cache_entry is None:
            # Send unavailable message and close
            unavailable_msg = WSReplayUnavailableMessage(
                trial_id=trial_id,
                reason=cast(ReplayErrorReason, error_reason),
                timestamp=datetime.now(timezone.utc),
            )
            await websocket.send_text(unavailable_msg.model_dump_json())
            await websocket.close()
            LOGGER.info("Replay unavailable for trial '%s': %s", trial_id, error_reason)
            return

        # Create controller with pre-computed meta
        controller = TrialReplayController(
            trial_id=trial_id,
            items=cache_entry.items,
            meta=cache_entry.meta,
            snapshot_size=snapshot_size,
        )

        # Initialize category filter from query params
        cat_filter = CategoryFilter.from_query(categories, filter_mode)

        try:
            # 1. Always send meta info first
            meta_msg = WSReplayMetaInfoMessage(
                trial_id=trial_id,
                total_items=len(cache_entry.items),
                total_play_count=cache_entry.meta.total_play_count,
                periods=[
                    {
                        "period": p.period,
                        "playCount": p.play_count,
                        "startPlayIndex": p.start_play_index,
                    }
                    for p in cache_entry.meta.periods
                ],
                timestamp=datetime.now(timezone.utc),
            )
            await websocket.send_text(meta_msg.model_dump_json())
            LOGGER.info(
                "Replay: Sent meta info for trial '%s' (plays=%d, periods=%d)",
                trial_id,
                cache_entry.meta.total_play_count,
                len(cache_entry.meta.periods),
            )

            # 2. If autostart, send initial snapshot; otherwise pause and wait
            if autostart:
                snapshot_items = controller.get_snapshot_items()
                # Apply category filter to snapshot
                filtered_snapshot = cat_filter.filter_items(snapshot_items)
                snapshot_msg = WSSnapshotMessage(
                    trial_id=trial_id,
                    timestamp=datetime.now(timezone.utc),
                    data={"items": filtered_snapshot},
                )
                await websocket.send_text(snapshot_msg.model_dump_json())
                LOGGER.info(
                    "Replay: Sent snapshot with %d items (filtered from %d) for trial '%s'",
                    len(filtered_snapshot),
                    len(snapshot_items),
                    trial_id,
                )
            else:
                # Pause immediately when autostart=false
                controller.pause()
                LOGGER.info("Replay: Waiting for resume command (autostart=false)")

            # Send initial status
            await websocket.send_text(controller.get_status().model_dump_json())

            # Track last heartbeat time (separate from playback timing)
            last_heartbeat_time = time.time()

            # Main replay loop
            while True:
                # Calculate timeout: use shorter of playback interval and time until next heartbeat
                playback_interval = controller.get_effective_interval()
                time_since_heartbeat = time.time() - last_heartbeat_time
                time_until_heartbeat = max(
                    0, controller.heartbeat_interval - time_since_heartbeat
                )

                # When paused, only wait for heartbeat; when playing, use shorter interval
                if controller.is_paused:
                    timeout = time_until_heartbeat
                else:
                    timeout = min(playback_interval, time_until_heartbeat)

                try:
                    # Wait for command or timeout
                    msg_text = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=max(timeout, 0.1),  # Minimum 100ms to avoid busy loop
                    )

                    # Handle command
                    try:
                        command_data = json.loads(msg_text)
                        command = command_data.get("command", "")
                        value = command_data.get("value")

                        if command == "pause":
                            controller.pause()
                        elif command == "resume":
                            # If first resume after autostart=false, send snapshot
                            if not autostart and controller.current_index == 0:
                                snapshot_items = controller.get_snapshot_items()
                                filtered_snapshot = cat_filter.filter_items(
                                    snapshot_items
                                )
                                snapshot_msg = WSSnapshotMessage(
                                    trial_id=trial_id,
                                    timestamp=datetime.now(timezone.utc),
                                    data={"items": filtered_snapshot},
                                )
                                await websocket.send_text(
                                    snapshot_msg.model_dump_json()
                                )
                            controller.resume()
                        elif command == "speed" and value is not None:
                            controller.set_speed(float(value))
                        elif command == "reset":
                            controller.reset()
                            # Re-send snapshot
                            snapshot_items = controller.get_snapshot_items()
                            filtered_snapshot = cat_filter.filter_items(snapshot_items)
                            snapshot_msg = WSSnapshotMessage(
                                trial_id=trial_id,
                                timestamp=datetime.now(timezone.utc),
                                data={"items": filtered_snapshot},
                            )
                            await websocket.send_text(snapshot_msg.model_dump_json())
                        elif command == "seek":
                            play_index = command_data.get("play_index", 0)
                            seek_items = controller.seek_to_play_index(int(play_index))
                            filtered_seek = cat_filter.filter_items(seek_items)
                            snapshot_msg = WSSnapshotMessage(
                                trial_id=trial_id,
                                timestamp=datetime.now(timezone.utc),
                                data={"items": filtered_seek},
                            )
                            await websocket.send_text(snapshot_msg.model_dump_json())
                            LOGGER.debug(
                                "Replay: Seeked to play_index %d for trial '%s'",
                                play_index,
                                trial_id,
                            )
                        elif command == "filter":
                            # Update category filter dynamically
                            filter_categories = command_data.get("categories", [])
                            filter_mode_cmd = command_data.get("mode", "include")
                            cat_filter = CategoryFilter.from_list(
                                filter_categories, filter_mode_cmd
                            )
                            LOGGER.debug(
                                "Replay: Updated filter for trial '%s': categories=%s, mode=%s",
                                trial_id,
                                filter_categories,
                                filter_mode_cmd,
                            )
                        elif command == "status":
                            pass  # Just send status below
                        elif command == "disconnect":
                            LOGGER.info(
                                "Replay: Client requested disconnect for trial '%s'",
                                trial_id,
                            )
                            return
                        else:
                            LOGGER.warning("Unknown replay command: %s", command)
                            continue

                        # Send status after any command
                        await websocket.send_text(
                            controller.get_status().model_dump_json()
                        )

                    except json.JSONDecodeError:
                        LOGGER.warning("Invalid JSON command: %s", msg_text)

                except asyncio.TimeoutError:
                    # Check if we need to send heartbeat (fixed interval)
                    if (
                        time.time() - last_heartbeat_time
                        >= controller.heartbeat_interval
                    ):
                        heartbeat = WSHeartbeatMessage(
                            timestamp=datetime.now(timezone.utc),
                        )
                        await websocket.send_text(heartbeat.model_dump_json())
                        last_heartbeat_time = time.time()

                    # Skip playback logic if paused
                    if controller.is_paused:
                        continue

                    if controller.is_complete():
                        # Send trial ended
                        ended_msg = WSTrialEndedMessage(
                            trial_id=trial_id,
                            timestamp=datetime.now(timezone.utc),
                        )
                        await websocket.send_text(ended_msg.model_dump_json())
                        LOGGER.info("Replay completed for trial '%s'", trial_id)

                        # Pause at end, allow reset/seek
                        controller.pause()
                        await websocket.send_text(
                            controller.get_status().model_dump_json()
                        )
                        continue

                    # Send next item (apply category filter)
                    item = controller.get_next_item()
                    if item:
                        # Skip items that don't match the filter
                        if not cat_filter.filter_item(item):
                            continue

                        span_msg = WSSpanMessage(
                            trial_id=trial_id,
                            timestamp=datetime.now(timezone.utc),
                            category=item.get("category", ""),
                            data=item.get("data", {}),
                        )
                        await websocket.send_text(span_msg.model_dump_json())

                        # Send status every 20 items
                        if controller.current_index % 20 == 0:
                            await websocket.send_text(
                                controller.get_status().model_dump_json()
                            )

        except WebSocketDisconnect:
            LOGGER.info("Replay WebSocket disconnected for trial '%s'", trial_id)
        except Exception as e:
            LOGGER.error("Replay WebSocket error for trial '%s': %s", trial_id, e)
