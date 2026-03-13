"""Social media data collection for Twitter/X."""

from dojozero.data.socialmedia._events import (
    SocialMediaEventMixin,
    TwitterTopTweetsEvent,
)
from dojozero.data.socialmedia._watchlist import (
    BaseWatchlistRegistry,
    GameWatchlist,
    NBAWatchlistRegistry,
    NFLWatchlistRegistry,
    SocialAccount,
    get_nba_registry,
    get_nfl_registry,
    get_registry,
)
from dojozero.data.socialmedia._api import SocialMediaAPI
from dojozero.data.socialmedia._store import SocialMediaStore
from dojozero.data.socialmedia._factory import SocialMediaStoreFactory

__all__ = [
    "SocialMediaEventMixin",
    "TwitterTopTweetsEvent",
    # Watchlist exports
    "BaseWatchlistRegistry",
    "GameWatchlist",
    "NBAWatchlistRegistry",
    "NFLWatchlistRegistry",
    "SocialAccount",
    "get_nba_registry",
    "get_nfl_registry",
    "get_registry",
    # Store and API exports
    "SocialMediaAPI",
    "SocialMediaStore",
    "SocialMediaStoreFactory",
]
