"""Tests for social media event processing and lifecycle."""

from unittest.mock import patch

import pytest

from dojozero.data.socialmedia._api import SocialMediaAPI
from dojozero.data.socialmedia._events import (
    TwitterTopTweetsEvent,
)
from dojozero.data._context import GameContext


class TestFromSocialMedia:
    """Test the full from_social_media lifecycle using X API."""

    @pytest.fixture
    def game_context(self):
        """Create a GameContext for testing."""
        return GameContext(
            sport="nba",
            home_team="Lakers",
            away_team="Warriors",
            home_tricode="LAL",
            away_tricode="GSW",
            game_date="2024-01-15",
            game_id="test_game_123",
        )

    @pytest.mark.asyncio
    @patch("dojozero.data.socialmedia._events.summarize_content")
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.__init__", return_value=None)
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.fetch")
    async def test_twitter_success(
        self, mock_api_fetch, mock_init, mock_summarize, game_context
    ):
        """Test successful Twitter tweet collection with per-account summarization."""
        # Mock API response with tweets from 2 accounts
        mock_api_fetch.return_value = {
            "tweets": [
                {
                    "text": "Lakers vs Warriors tonight!",
                    "url": "https://x.com/Lakers/status/1234567890",
                    "username": "Lakers",
                    "tweet_id": "1234567890",
                },
                {
                    "text": "Excited for tonight's game between Lakers and Warriors.",
                    "url": "https://x.com/jovanbuha/status/1234567891",
                    "username": "jovanbuha",
                    "tweet_id": "1234567891",
                },
            ],
            "query": "watchlist: 2 accounts (Lakers vs Warriors)",
            "game_id": "test_game_123",
            "sport": "nba",
        }

        # Mock summarization - called once per account
        def mock_summarize_side_effect(batch_text, **kwargs):
            if "Lakers" in batch_text:
                return "KEY POINTS:\n- [GAME INFO] Lakers vs Warriors tonight (2024-01-15)\n\nSIGNAL:\nGame preview for tonight's matchup."
            elif "jovanbuha" in batch_text or "Excited" in batch_text:
                return "KEY POINTS:\n- [GAME INFO] Beat reporter preview for tonight's game (2024-01-15)\n\nSIGNAL:\nAnticipation for the matchup."
            return None

        mock_summarize.side_effect = mock_summarize_side_effect

        api = SocialMediaAPI()
        event = await TwitterTopTweetsEvent.from_social_media(
            api=api,
            context=game_context,
        )

        assert event is not None
        assert event.event_type == "event.twitter_top_tweets"
        assert len(event.tweets) == 2
        assert len(event.posts) == 0  # Twitter events have empty posts
        assert event.game_id == "test_game_123"
        assert event.sport == "nba"
        assert event.source == "twitter"
        assert "watchlist" in event.query.lower()
        # Summary should contain account labels
        assert "[@Lakers]" in event.summary
        assert "[@jovanbuha]" in event.summary
        assert "KEY POINTS" in event.summary
        # Verify summarize_content was called twice (once per account)
        assert mock_summarize.call_count == 2

    @pytest.mark.asyncio
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.__init__", return_value=None)
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.fetch")
    async def test_no_results(self, mock_api_fetch, mock_init, game_context):
        """Test handling when no tweets are found."""
        # Mock API response with no tweets
        mock_api_fetch.return_value = {
            "tweets": [],
            "query": "watchlist: 0 accounts (Lakers vs Warriors)",
            "game_id": "test_game_123",
            "sport": "nba",
        }

        api = SocialMediaAPI()
        event = await TwitterTopTweetsEvent.from_social_media(
            api=api,
            context=game_context,
        )

        assert event is None

    @pytest.mark.asyncio
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.__init__", return_value=None)
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.fetch")
    async def test_api_error(self, mock_api_fetch, mock_init, game_context):
        """Test handling when API call fails."""
        mock_api_fetch.side_effect = Exception("API error")

        api = SocialMediaAPI()
        event = await TwitterTopTweetsEvent.from_social_media(
            api=api,
            context=game_context,
        )

        assert event is None

    @pytest.mark.asyncio
    @patch("dojozero.data.socialmedia._events.summarize_content")
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.__init__", return_value=None)
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.fetch")
    async def test_summarization_timeout(
        self, mock_api_fetch, mock_init, mock_summarize, game_context
    ):
        """Test handling when summarization times out for an account."""
        import asyncio

        # Mock API response with tweets from one account
        mock_api_fetch.return_value = {
            "tweets": [
                {
                    "text": "Lakers vs Warriors tonight!",
                    "url": "https://x.com/Lakers/status/1234567890",
                    "username": "Lakers",
                    "tweet_id": "1234567890",
                },
            ],
            "query": "watchlist: 1 account (Lakers vs Warriors)",
            "game_id": "test_game_123",
            "sport": "nba",
        }

        # Mock summarization to timeout
        mock_summarize.side_effect = asyncio.TimeoutError()

        api = SocialMediaAPI()
        event = await TwitterTopTweetsEvent.from_social_media(
            api=api,
            context=game_context,
            summarize_timeout=0.1,  # Very short timeout
        )

        # Should still return event but with empty summary (account filtered out)
        assert event is not None
        assert len(event.tweets) == 1
        assert event.summary == ""  # Empty due to timeout (account filtered out)

    @pytest.mark.asyncio
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.__init__", return_value=None)
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.fetch")
    async def test_unsupported_sport(self, mock_api_fetch, mock_init):
        """Test handling when sport is not supported."""
        context = GameContext(
            sport="soccer",  # Unsupported sport
            home_team="Team A",
            away_team="Team B",
            home_tricode="TA",
            away_tricode="TB",
            game_date="2024-01-15",
            game_id="test_game_123",
        )

        # Mock API to return empty results for unsupported sport
        mock_api_fetch.return_value = {
            "tweets": [],
            "query": "watchlist: 0 accounts (Team A vs Team B)",
            "game_id": "test_game_123",
            "sport": "soccer",
        }

        api = SocialMediaAPI()
        event = await TwitterTopTweetsEvent.from_social_media(
            api=api,
            context=context,
        )

        assert event is None

    @pytest.mark.asyncio
    @patch("dojozero.data.socialmedia._events.summarize_content")
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.__init__", return_value=None)
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.fetch")
    async def test_filter_irrelevant_accounts(
        self, mock_api_fetch, mock_init, mock_summarize, game_context
    ):
        """Test that accounts with no relevant content are filtered out."""
        # Mock API response with tweets from 3 accounts
        mock_api_fetch.return_value = {
            "tweets": [
                {
                    "text": "Lakers vs Warriors tonight!",
                    "url": "https://x.com/Lakers/status/1234567890",
                    "username": "Lakers",
                    "tweet_id": "1234567890",
                },
                {
                    "text": "Check out our new merchandise!",
                    "url": "https://x.com/Lakers/status/1234567891",
                    "username": "Lakers",
                    "tweet_id": "1234567891",
                },
                {
                    "text": "Game preview: Lakers vs Warriors",
                    "url": "https://x.com/jovanbuha/status/1234567892",
                    "username": "jovanbuha",
                    "tweet_id": "1234567892",
                },
                {
                    "text": "Random tweet about weather",
                    "url": "https://x.com/random_account/status/1234567893",
                    "username": "random_account",
                    "tweet_id": "1234567893",
                },
            ],
            "query": "watchlist: 3 accounts (Lakers vs Warriors)",
            "game_id": "test_game_123",
            "sport": "nba",
        }

        # Mock summarization - Lakers and jovanbuha have relevant content, random_account doesn't
        def mock_summarize_side_effect(batch_text, **kwargs):
            if "Lakers" in batch_text and "tonight" in batch_text:
                return "KEY POINTS:\n- [GAME INFO] Lakers vs Warriors tonight (2024-01-15)\n\nSIGNAL:\nGame preview."
            elif "jovanbuha" in batch_text or "Game preview" in batch_text:
                return "KEY POINTS:\n- [GAME INFO] Beat reporter preview (2024-01-15)\n\nSIGNAL:\nMatchup analysis."
            elif "random_account" in batch_text or "weather" in batch_text:
                return None  # Irrelevant content
            return None

        mock_summarize.side_effect = mock_summarize_side_effect

        api = SocialMediaAPI()
        event = await TwitterTopTweetsEvent.from_social_media(
            api=api,
            context=game_context,
        )

        assert event is not None
        assert len(event.tweets) == 4  # All tweets still in event
        # Summary should only include relevant accounts
        assert "[@Lakers]" in event.summary
        assert "[@jovanbuha]" in event.summary
        assert "[@random_account]" not in event.summary
        # Verify summarize_content was called 3 times (once per account)
        assert mock_summarize.call_count == 3

    @pytest.mark.asyncio
    @patch("dojozero.data.socialmedia._events.summarize_content")
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.__init__", return_value=None)
    @patch("dojozero.data.socialmedia._api.SocialMediaAPI.fetch")
    async def test_empty_account_tweets_filtered(
        self, mock_api_fetch, mock_init, mock_summarize, game_context
    ):
        """Test that accounts with empty tweet lists are skipped."""
        # Mock API response - one account has tweets, another would be empty
        mock_api_fetch.return_value = {
            "tweets": [
                {
                    "text": "Lakers vs Warriors tonight!",
                    "url": "https://x.com/Lakers/status/1234567890",
                    "username": "Lakers",
                    "tweet_id": "1234567890",
                },
            ],
            "query": "watchlist: 2 accounts (Lakers vs Warriors)",
            "game_id": "test_game_123",
            "sport": "nba",
        }

        # Mock summarization
        mock_summarize.return_value = "KEY POINTS:\n- [GAME INFO] Lakers vs Warriors tonight (2024-01-15)\n\nSIGNAL:\nGame preview."

        api = SocialMediaAPI()
        event = await TwitterTopTweetsEvent.from_social_media(
            api=api,
            context=game_context,
        )

        assert event is not None
        # Only one account has tweets, so summarize_content should be called once
        assert mock_summarize.call_count == 1
        assert "[@Lakers]" in event.summary


class TestEventFields:
    """Test event field validation and defaults."""

    def test_twitter_event_defaults(self):
        """Test TwitterTopTweetsEvent has correct defaults."""
        event = TwitterTopTweetsEvent()
        assert event.event_type == "event.twitter_top_tweets"
        assert event.posts == []
        assert event.tweets == []
        assert event.summary == ""
        assert event.default_search_template == "{home_team} vs {away_team} {sport}"

    def test_twitter_event_with_data(self):
        """Test TwitterTopTweetsEvent with actual data."""
        event = TwitterTopTweetsEvent(
            tweets=[
                {
                    "text": "Test tweet",
                    "username": "nba",
                    "url": "https://x.com/nba/status/1234567890",
                    "tweet_id": "1234567890",
                }
            ],
            query="watchlist: 8 accounts (Lakers vs Warriors)",
            summary="KEY POINTS:\n- [GAME INFO] Test game (2024-01-15)\n\nSIGNAL:\nTest signal.",
            game_id="test_game_123",
            sport="nba",
            source="twitter",
        )
        assert len(event.tweets) == 1
        assert event.tweets[0]["username"] == "nba"
        assert len(event.posts) == 0
        assert "KEY POINTS" in event.summary