"""Tests for GameStateTracker - NBA game state management."""

import pytest

from dojozero.data.nba._state_tracker import GameStateTracker


@pytest.fixture
def tracker():
    """Create a fresh GameStateTracker instance."""
    return GameStateTracker()


class TestGameStatusTracking:
    """Tests for game status tracking."""

    def test_get_previous_status_returns_none_for_unseen_game(self, tracker):
        """Test that unseen games return None for status."""
        assert tracker.get_previous_status("game123") is None

    def test_set_and_get_previous_status(self, tracker):
        """Test setting and getting game status."""
        tracker.set_previous_status("game123", 2)  # In Progress

        assert tracker.get_previous_status("game123") == 2

    def test_multiple_games_tracked_separately(self, tracker):
        """Test that multiple games are tracked independently."""
        tracker.set_previous_status("game1", 1)  # Scheduled
        tracker.set_previous_status("game2", 2)  # In Progress
        tracker.set_previous_status("game3", 3)  # Finished

        assert tracker.get_previous_status("game1") == 1
        assert tracker.get_previous_status("game2") == 2
        assert tracker.get_previous_status("game3") == 3

    def test_status_can_be_updated(self, tracker):
        """Test that game status can be updated."""
        tracker.set_previous_status("game123", 1)
        tracker.set_previous_status("game123", 2)
        tracker.set_previous_status("game123", 3)

        assert tracker.get_previous_status("game123") == 3


class TestEventDeduplication:
    """Tests for event deduplication."""

    def test_has_seen_event_returns_false_for_new_event(self, tracker):
        """Test that new events return False."""
        assert tracker.has_seen_event("game123_pbp_1") is False

    def test_has_seen_event_returns_true_after_marking(self, tracker):
        """Test that seen events return True."""
        tracker.mark_event_seen("game123_pbp_1")

        assert tracker.has_seen_event("game123_pbp_1") is True

    def test_multiple_events_tracked(self, tracker):
        """Test that multiple events are tracked."""
        tracker.mark_event_seen("game123_pbp_1")
        tracker.mark_event_seen("game123_pbp_2")

        assert tracker.has_seen_event("game123_pbp_1") is True
        assert tracker.has_seen_event("game123_pbp_2") is True
        assert tracker.has_seen_event("game123_pbp_3") is False

    def test_event_ids_are_unique_strings(self, tracker):
        """Test that event IDs work as unique strings (include game ID in event ID)."""
        # Event IDs should include game ID to be unique per game
        tracker.mark_event_seen("game1_pbp_1")
        tracker.mark_event_seen("game2_pbp_1")

        assert tracker.has_seen_event("game1_pbp_1") is True
        assert tracker.has_seen_event("game1_pbp_2") is False
        assert tracker.has_seen_event("game2_pbp_1") is True


class TestPbpAvailability:
    """Tests for play-by-play availability tracking."""

    def test_is_pbp_available_returns_false_initially(self, tracker):
        """Test that PBP is not available initially."""
        assert tracker.is_pbp_available("game123") is False

    def test_mark_pbp_available(self, tracker):
        """Test marking PBP as available."""
        tracker.mark_pbp_available("game123")

        assert tracker.is_pbp_available("game123") is True

    def test_pbp_tracked_separately_per_game(self, tracker):
        """Test that PBP availability is tracked per game."""
        tracker.mark_pbp_available("game1")

        assert tracker.is_pbp_available("game1") is True
        assert tracker.is_pbp_available("game2") is False


class TestGameInitialization:
    """Tests for game initialization tracking."""

    def test_is_game_initialized_returns_false_initially(self, tracker):
        """Test that games are not initialized initially."""
        assert tracker.is_game_initialized("game123") is False

    def test_mark_game_initialized(self, tracker):
        """Test marking a game as initialized."""
        tracker.mark_game_initialized("game123")

        assert tracker.is_game_initialized("game123") is True

    def test_initialization_tracked_separately_per_game(self, tracker):
        """Test that initialization is tracked per game."""
        tracker.mark_game_initialized("game1")

        assert tracker.is_game_initialized("game1") is True
        assert tracker.is_game_initialized("game2") is False


class TestFilterNewActions:
    """Tests for filtering new play-by-play actions."""

    def test_filter_new_actions_returns_all_for_unseen_game(self, tracker):
        """Test that all actions are returned for unseen game."""
        actions = [
            {"actionNumber": 1, "actionType": "shot"},
            {"actionNumber": 2, "actionType": "rebound"},
            {"actionNumber": 3, "actionType": "foul"},
        ]

        new_actions = tracker.filter_new_actions("game123", actions)

        assert len(new_actions) == 3
        assert new_actions == actions

    def test_filter_new_actions_marks_as_seen(self, tracker):
        """Test that filtered actions are marked as seen."""
        actions = [
            {"actionNumber": 1, "actionType": "shot"},
            {"actionNumber": 2, "actionType": "rebound"},
        ]

        tracker.filter_new_actions("game123", actions)

        # Second call should return empty
        new_actions = tracker.filter_new_actions("game123", actions)
        assert len(new_actions) == 0

    def test_filter_new_actions_returns_only_new(self, tracker):
        """Test that only new actions are returned."""
        # First batch
        actions1 = [
            {"actionNumber": 1, "actionType": "shot"},
            {"actionNumber": 2, "actionType": "rebound"},
        ]
        tracker.filter_new_actions("game123", actions1)

        # Second batch with some overlap
        actions2 = [
            {"actionNumber": 2, "actionType": "rebound"},  # Already seen
            {"actionNumber": 3, "actionType": "foul"},  # New
            {"actionNumber": 4, "actionType": "shot"},  # New
        ]
        new_actions = tracker.filter_new_actions("game123", actions2)

        assert len(new_actions) == 2
        assert new_actions[0]["actionNumber"] == 3
        assert new_actions[1]["actionNumber"] == 4

    def test_filter_new_actions_handles_empty_list(self, tracker):
        """Test that empty action list is handled."""
        new_actions = tracker.filter_new_actions("game123", [])

        assert new_actions == []

    def test_filter_new_actions_handles_non_dict_items(self, tracker):
        """Test that non-dict items are filtered out."""
        actions = [
            {"actionNumber": 1, "actionType": "shot"},
            None,  # Non-dict
            "invalid",  # Non-dict
            {"actionNumber": 2, "actionType": "rebound"},
        ]

        new_actions = tracker.filter_new_actions("game123", actions)

        assert len(new_actions) == 2
        assert all(isinstance(a, dict) for a in new_actions)

    def test_filter_new_actions_handles_missing_action_number(self, tracker):
        """Test that actions without actionNumber are handled."""
        actions = [
            {"actionNumber": 1, "actionType": "shot"},
            {"actionType": "timeout"},  # No actionNumber
            {"actionNumber": 2, "actionType": "rebound"},
        ]

        new_actions = tracker.filter_new_actions("game123", actions)

        # Should include actions without actionNumber (they can't be deduplicated)
        assert len(new_actions) == 3

    def test_filter_new_actions_separate_per_game(self, tracker):
        """Test that action filtering is separate per game."""
        actions = [{"actionNumber": 1, "actionType": "shot"}]

        tracker.filter_new_actions("game1", actions)

        # Same action for different game should be returned
        new_actions = tracker.filter_new_actions("game2", actions)
        assert len(new_actions) == 1


class TestBoxscoreCache:
    """Tests for boxscore leaders cache."""

    def test_get_boxscore_cache_returns_none_initially(self, tracker):
        """Test that boxscore cache returns None initially."""
        assert tracker.get_boxscore_cache("game123") is None

    def test_set_and_get_boxscore_cache(self, tracker):
        """Test setting and getting boxscore cache."""
        cache_data = {"points_leader": "Player A", "rebounds_leader": "Player B"}
        tracker.set_boxscore_cache("game123", cache_data)

        assert tracker.get_boxscore_cache("game123") == cache_data

    def test_boxscore_cache_separate_per_game(self, tracker):
        """Test that boxscore cache is separate per game."""
        cache1 = {"points_leader": "Player A"}
        cache2 = {"points_leader": "Player B"}

        tracker.set_boxscore_cache("game1", cache1)
        tracker.set_boxscore_cache("game2", cache2)

        assert tracker.get_boxscore_cache("game1") == cache1
        assert tracker.get_boxscore_cache("game2") == cache2


class TestTrackerStateIndependence:
    """Tests for tracker state independence and isolation."""

    def test_new_tracker_has_no_state(self):
        """Test that a new tracker has no stored state."""
        from dojozero.data.nba._state_tracker import GameStateTracker

        tracker = GameStateTracker()

        assert tracker.get_previous_status("game123") is None
        assert tracker.has_seen_event("game123_pbp_1") is False
        assert tracker.is_pbp_available("game123") is False
        assert tracker.is_game_initialized("game123") is False
        assert tracker.get_boxscore_cache("game123") is None

    def test_separate_trackers_are_independent(self):
        """Test that separate tracker instances don't share state."""
        from dojozero.data.nba._state_tracker import GameStateTracker

        tracker1 = GameStateTracker()
        tracker2 = GameStateTracker()

        # Set state in tracker1
        tracker1.set_previous_status("game123", 2)
        tracker1.mark_event_seen("game123_pbp_1")
        tracker1.mark_pbp_available("game123")

        # tracker2 should be unaffected
        assert tracker2.get_previous_status("game123") is None
        assert tracker2.has_seen_event("game123_pbp_1") is False
        assert tracker2.is_pbp_available("game123") is False
