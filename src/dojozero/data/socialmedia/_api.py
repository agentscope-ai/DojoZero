"""Social Media ExternalAPI implementation with X API (xdk) integration."""

import asyncio
import os
from typing import Any

from dojozero.data._stores import ExternalAPI
from dojozero.data._context import GameContext

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, skip .env loading

# Try to import X API client
try:
    from xdk import Client as XClient

    XDK_AVAILABLE = True
except ImportError:
    XDK_AVAILABLE = False
    XClient = None  # type: ignore[assignment, misc]


class SocialMediaAPI(ExternalAPI):
    """Social Media API implementation with X API (xdk) support."""

    def __init__(self, bearer_token: str | None = None):
        """Initialize Social Media API.

        Args:
            bearer_token: X API bearer token (defaults to DOJOZERO_X_API_BEARER_TOKEN env var)
        """
        if not XDK_AVAILABLE or XClient is None:
            raise ImportError(
                "X API SDK (xdk) not installed. Install with: pip install xdk"
            )

        self.bearer_token = bearer_token or os.getenv("DOJOZERO_X_API_BEARER_TOKEN")
        if not self.bearer_token:
            raise ValueError(
                "DOJOZERO_X_API_BEARER_TOKEN not provided and not found in environment variables. "
                "Please set it in .env file or pass as parameter."
            )

        self._client: Any = None

    @property
    def client(self) -> Any:
        """Get or create X API client."""
        if self._client is None:
            if XClient is None:
                raise ImportError("XClient is not available")
            if self.bearer_token is None:
                raise ValueError("Bearer token is required but not set")
            self._client = XClient(bearer_token=self.bearer_token)
        return self._client

    def _search_account_posts(
        self,
        username: str,
        description: str = "",
        home_team: str = "",
        away_team: str = "",
        home_tricode: str = "",
        away_tricode: str = "",
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search recent posts from a specific account.

        For betting/analytics accounts, adds team keywords to filter relevant tweets.

        Args:
            username: Twitter username to search
            description: Account description (used to determine search strategy)
            home_team: Home team name
            away_team: Away team name
            home_tricode: Home team tricode
            away_tricode: Away team tricode
            max_results: Maximum number of tweets to fetch per account (default: 10)

        Returns:
            List of tweet dictionaries with 'text', 'url', 'username', 'tweet_id' keys
        """
        # For betting/analytics accounts, add team keywords to filter relevant tweets
        if "Betting/analytics analyst" in description and home_team and away_team:
            query = f'from:{username} ("{home_team}" OR "{away_team}" OR "{home_tricode}" OR "{away_tricode}")'
        else:
            query = f"from:{username}"

        tweets = []

        try:
            # Use next() to get only the FIRST page, preventing pagination
            page_iterator = self.client.posts.search_recent(
                query=query,
                max_results=max_results,
                tweet_fields=["created_at", "author_id", "public_metrics", "text"],
            )
            first_page = next(page_iterator, None)

            if first_page is None:
                return []

            page_data = getattr(first_page, "data", []) or []

            for post in page_data:
                text = (
                    post.get("text", "")
                    if isinstance(post, dict)
                    else getattr(post, "text", "")
                )
                post_id = (
                    post.get("id", "")
                    if isinstance(post, dict)
                    else getattr(post, "id", "")
                )
                if text:
                    tweets.append(
                        {
                            "text": text,
                            "url": f"https://x.com/{username}/status/{post_id}",
                            "username": username,
                            "tweet_id": post_id,
                        }
                    )
        except StopIteration:
            # No results, return empty list
            return []
        except Exception:
            # No results, return empty list
            return []

        return tweets

    async def fetch(
        self, endpoint: str = "search", params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch social media data from watchlist accounts.

        Args:
            endpoint: API endpoint (currently only "search" is supported)
            params: Search parameters containing:
                - context: GameContext with team/date info (required)
                - max_tweets_per_account: Maximum tweets per account (default: 10)
                - account_timeout: Timeout per account in seconds (default: 30.0)

        Returns:
            Search results in standardized format with:
                - query: Search query description
                - tweets: List of aggregated tweets
                - game_id: ESPN game ID
                - sport: Sport identifier
        """
        if endpoint != "search":
            raise NotImplementedError(
                f"Invalid endpoint: {endpoint}. Only 'search' is supported."
            )

        if not params:
            return {
                "query": "",
                "tweets": [],
                "game_id": "",
                "sport": "",
            }

        context: GameContext | None = params.get("context")
        if not context or not isinstance(context, GameContext):
            raise ValueError("GameContext is required in params")

        max_tweets_per_account = params.get("max_tweets_per_account", 10)
        account_timeout = params.get("account_timeout", 30.0)

        # Import here to avoid circular dependencies
        from dojozero.data.socialmedia import get_registry

        # 1. Get appropriate watchlist registry
        try:
            registry = get_registry(context.sport)
        except ValueError:
            return {
                "query": "",
                "tweets": [],
                "game_id": context.game_id,
                "sport": context.sport,
            }

        # 2. Build game watchlist
        watchlist = registry.build_game_watchlist(
            context.home_tricode,
            context.away_tricode,
        )

        if not watchlist.accounts:
            return {
                "query": "",
                "tweets": [],
                "game_id": context.game_id,
                "sport": context.sport,
            }

        # 3. Fetch tweets from each account (with timeout)
        all_tweets = []

        for acct in watchlist.accounts:
            try:
                # Search with timeout
                tweets = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._search_account_posts,
                        acct.username,
                        acct.description or "",
                        context.home_team,
                        context.away_team,
                        context.home_tricode,
                        context.away_tricode,
                        max_tweets_per_account,
                    ),
                    timeout=account_timeout,
                )

                if tweets:
                    all_tweets.extend(tweets)

            except asyncio.TimeoutError:
                # Skip this account on timeout
                continue
            except Exception:
                # Skip this account on error
                continue

        # Build query description
        query = f"watchlist: {len(watchlist.accounts)} accounts ({context.home_team} vs {context.away_team})"

        return {
            "query": query,
            "tweets": all_tweets,
            "game_id": context.game_id,
            "sport": context.sport,
        }


__all__ = ["SocialMediaAPI"]
