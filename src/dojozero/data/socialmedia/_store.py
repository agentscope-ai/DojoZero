"""Social Media data store implementation."""

from typing import Any, Sequence

from dojozero.data._models import DataEvent
from dojozero.data._stores import DataStore, ExternalAPI
from dojozero.data.socialmedia._api import SocialMediaAPI
from dojozero.data.socialmedia._events import TwitterTopTweetsEvent
from dojozero.data._context import GameContext


class SocialMediaStore(DataStore):
    """Social Media data store for querying X API and emitting events.

    Note: This store does not poll automatically. It only emits events when
    search() is called explicitly.
    """

    def __init__(
        self,
        store_id: str = "social_media_store",
        api: ExternalAPI | None = None,
        event_emitter=None,
    ):
        """Initialize Social Media store."""
        super().__init__(
            store_id, api=api or SocialMediaAPI(), event_emitter=event_emitter
        )

    async def start_polling(self) -> None:
        """Override to prevent automatic polling.

        SocialMediaStore should only be triggered by explicit search() calls,
        not by polling. This prevents errors when DataHub.start() is called.
        """
        pass

    async def search(
        self,
        context: GameContext,
        max_tweets_per_account: int = 10,
        account_timeout: float = 30.0,
        summarize_timeout: float = 60.0,
        **search_params: Any,
    ) -> None:
        """Trigger a social media search and emit events with summarization.

        Args:
            context: GameContext with team/date info
            max_tweets_per_account: Maximum tweets to fetch per account (default: 10)
            account_timeout: Timeout per account API call in seconds (default: 30.0)
            summarize_timeout: Timeout for summarization in seconds (default: 60.0)
            **search_params: Additional search parameters (currently unused)
        """
        assert self._api is not None, "API must be initialized"
        assert isinstance(self._api, SocialMediaAPI), "API must be SocialMediaAPI"

        # Use from_social_media() to get fully processed event with summary
        event = await TwitterTopTweetsEvent.from_social_media(
            api=self._api,
            context=context,
            max_tweets_per_account=max_tweets_per_account,
            account_timeout=account_timeout,
            summarize_timeout=summarize_timeout,
        )

        if event is not None:
            await self.emit_event(event)

    def _parse_api_response(
        self,
        data: dict[str, Any],
    ) -> Sequence[DataEvent]:
        """Parse Social Media API response into DataEvents.

        Note: This method is kept for backward compatibility but is not used
        by search() which calls from_social_media() directly for summarization.
        """
        from datetime import datetime, timezone

        query = data.get("query", "")
        tweets = data.get("tweets", [])
        game_id = data.get("game_id", "")
        sport = data.get("sport", "")

        if not tweets:
            return []

        return [
            TwitterTopTweetsEvent(
                timestamp=datetime.now(timezone.utc),
                query=query,
                tweets=tweets,
                summary="",  # No summary in raw parsing
                game_id=game_id,
                sport=sport,
                source="twitter",
            )
        ]


__all__ = ["SocialMediaStore"]
