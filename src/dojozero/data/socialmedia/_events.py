"""Social media event types for Twitter/X posts.

Uses X API (xdk) to search curated watchlist accounts for game-related tweets.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar, Literal, Self

from pydantic import Field

from dojozero.data._models import PreGameInsightEvent, register_event
from dojozero.data._utils import summarize_content
from dojozero.data.socialmedia._api import SocialMediaAPI
from dojozero.data._context import GameContext

logger = logging.getLogger(__name__)

# Note: For social media account registries (team accounts, beat reporters, betting analysts),
# see dojozero.data.socialmedia._watchlist module which provides:
# - NBAWatchlistRegistry and NFLWatchlistRegistry classes
# - Update methods
# - Structured account management


class SocialMediaEventMixin:
    """Mixin providing X API → typed-event lifecycle for social media events.

    - Fetches tweets from curated watchlist accounts using X API
    - Groups tweets by account
    - Summarizes each account's tweets separately with relevance filtering
    - Combines summaries from accounts with relevant content
    - Filters out accounts that provide no relevant information
    """

    default_search_template: ClassVar[str]

    @classmethod
    async def from_social_media(
        cls,
        api: SocialMediaAPI,
        context: GameContext,
        max_tweets_per_account: int = 10,
        account_timeout: float = 30.0,
        summarize_timeout: float = 60.0,
    ) -> Self | None:
        """Full lifecycle: build watchlist → fetch tweets → summarize per account → combine → typed event.

        Args:
            api: SocialMediaAPI instance for executing searches
            context: GameContext with team/date info
            max_tweets_per_account: Maximum tweets to fetch per account (default: 10)
            account_timeout: Timeout per account API call in seconds (default: 30.0)
            summarize_timeout: Timeout for summarization per account in seconds (default: 60.0)

        Returns:
            Typed event instance with aggregated tweets and combined summary, or None if no results.
            Accounts with no relevant content are filtered out from the summary.
        """
        logger.info(
            "SocialMedia query for %s: %s vs %s",
            cls.__name__,
            context.home_team,
            context.away_team,
        )

        # Call search API
        try:
            data = await api.fetch(
                "search",
                {
                    "context": context,
                    "max_tweets_per_account": max_tweets_per_account,
                    "account_timeout": account_timeout,
                },
            )
        except Exception as e:
            logger.warning("API call failed for %s: %s", cls.__name__, e)
            return None

        tweets = data.get("tweets", [])
        if not tweets:
            logger.warning(
                "No tweets found for %s query: %s vs %s",
                cls.__name__,
                context.home_team,
                context.away_team,
            )
            return None

        logger.info("%s returned %d tweets", cls.__name__, len(tweets))

        # Group tweets by account (username)
        tweets_by_account: dict[str, list[dict[str, Any]]] = {}
        for tweet in tweets:
            username = tweet.get("username", "unknown")
            if username not in tweets_by_account:
                tweets_by_account[username] = []
            tweets_by_account[username].append(tweet)

        logger.info("%s tweets from %d accounts", cls.__name__, len(tweets_by_account))

        game_ctx = {
            "home_team": context.home_team,
            "away_team": context.away_team,
            "game_date": context.game_date,
        }

        # Summarize each account's tweets separately
        account_summaries: list[tuple[str, str]] = []  # (username, summary)

        async def summarize_account(
            username: str, account_tweets: list[dict[str, Any]]
        ) -> tuple[str, str | None]:
            """Summarize tweets from a single account.

            Returns (username, summary) where summary is None if:
            - Account has no tweets (empty list)
            - All tweets are irrelevant
            - Summarization fails or times out
            """
            # Skip accounts with no tweets
            if not account_tweets:
                logger.debug("Skipping account @%s: no tweets", username)
                return (username, None)

            batch_text = "\n---\n".join(
                f"[Post {i + 1}]\n{t['text']}" for i, t in enumerate(account_tweets)
            )

            try:
                summary = await asyncio.wait_for(
                    summarize_content(
                        batch_text,
                        content_type="tweets",
                        game_context=game_ctx,
                    ),
                    timeout=summarize_timeout,
                )
                # summarize_content returns None if content is empty or irrelevant
                return (username, summary)
            except asyncio.TimeoutError:
                logger.warning(
                    "Summarization timeout for account @%s in %s",
                    username,
                    cls.__name__,
                )
                return (username, None)
            except Exception as e:
                logger.warning(
                    "Summarization error for account @%s in %s: %s",
                    username,
                    cls.__name__,
                    e,
                )
                return (username, None)

        # Summarize all accounts concurrently (only accounts with tweets)
        # Accounts with empty tweet lists will be skipped in summarize_account
        tasks = [
            summarize_account(username, account_tweets)
            for username, account_tweets in tweets_by_account.items()
        ]
        results = await asyncio.gather(*tasks)

        # Filter out accounts with no relevant content (None summaries)
        account_summaries = [
            (username, summary) for username, summary in results if summary is not None
        ]

        # Combine summaries from all accounts
        if account_summaries:
            summary_parts = []
            for username, account_summary in account_summaries:
                summary_parts.append(f"[@{username}]\n{account_summary}")
            summary = "\n\n".join(summary_parts)
        else:
            summary = None

        # Create event with aggregated tweets and summary
        query = data.get("query", "")
        event_data = {
            "tweets": tweets,
            "query": query,
            "summary": summary or "",
            "game_id": data.get("game_id", context.game_id),
            "sport": data.get("sport", context.sport),
            "source": "twitter",
        }
        event = cls.model_validate(event_data)  # type: ignore[attr-defined]

        return event


@register_event
class TwitterTopTweetsEvent(SocialMediaEventMixin, PreGameInsightEvent):
    """Top tweets from curated watchlist accounts about the game.

    Aggregates tweets from team accounts, beat reporters, and betting analysts
    for a specific game matchup. Content is summarized with relevance filtering
    for agent consumption.
    """

    event_type: Literal["event.twitter_top_tweets"] = "event.twitter_top_tweets"
    default_search_template: ClassVar[str] = "{home_team} vs {away_team} {sport}"

    query: str = ""  # Search query description (e.g., "watchlist: 8 accounts (Lakers vs Warriors)")
    summary: str = (
        ""  # Human-readable summary of processed tweets (KEY POINTS + SIGNAL format)
    )
    posts: list[dict[str, Any]] = Field(
        default_factory=list
    )  # Empty for Twitter events
    tweets: list[dict[str, Any]] = Field(
        default_factory=list
    )  # List of tweet dicts with text, url, username, tweet_id
