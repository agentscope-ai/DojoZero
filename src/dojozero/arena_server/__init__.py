"""Arena Server package for DojoZero.

This package provides the Arena Server functionality including:
- REST API for trial/span queries from Jaeger/SLS
- WebSocket streaming for real-time span updates
- Landing page data with caching
- Static file serving for React frontend

Moved from core module to keep server-related code separate from core abstractions.
"""

from ._server import (
    ArenaServerState,
    CacheEntry,
    LandingPageCache,
    SpanBroadcaster,
    WSMessageType,
    create_arena_app,
    get_server_state,
    run_arena_server,
)

__all__ = [
    # Server
    "ArenaServerState",
    "CacheEntry",
    "LandingPageCache",
    "SpanBroadcaster",
    "WSMessageType",
    "create_arena_app",
    "get_server_state",
    "run_arena_server",
]
