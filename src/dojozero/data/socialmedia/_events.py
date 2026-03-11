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
from dojozero.data.websearch._context import GameContext

logger = logging.getLogger(__name__)

# Note: For social media account registries (team accounts, beat reporters, betting analysts),
# see dojozero.data.socialmedia._watchlist module which provides:
# - NBAWatchlistRegistry and NFLWatchlistRegistry classes
# - Update methods
# - Structured account management


class SocialMediaEventMixin:
    """Mixin providing X API → typed-event lifecycle for social media events.

    - Fetches tweets from curated watchlist accounts using X API
    - Aggregates tweets from all accounts
    - Summarizes content with relevance filtering for agents
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
        """Full lifecycle: build watchlist → fetch tweets → aggregate → summarize → typed event.

        Args:
            api: SocialMediaAPI instance for executing searches
            context: GameContext with team/date info
            max_tweets_per_account: Maximum tweets to fetch per account (default: 10)
            account_timeout: Timeout per account API call in seconds (default: 30.0)
            summarize_timeout: Timeout for summarization in seconds (default: 60.0)

        Returns:
            Typed event instance with aggregated tweets and summary, or None if no results
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

        # Summarize aggregated tweets
        batch_text = "\n---\n".join(
            f"[Post {i + 1}]\n{t['text']}" for i, t in enumerate(tweets)
        )

        game_ctx = {
            "home_team": context.home_team,
            "away_team": context.away_team,
            "game_date": context.game_date,
        }

        summary: str | None = None
        try:
            summary = await asyncio.wait_for(
                summarize_content(
                    batch_text,
                    content_type="tweets",
                    game_context=game_ctx,
                ),
                timeout=summarize_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Summarization timeout for %s", cls.__name__)
        except Exception as e:
            logger.warning("Summarization error for %s: %s", cls.__name__, e)

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
