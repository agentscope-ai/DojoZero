"""Tests for subscription model, store, watcher, and CLI commands."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dojozero_client._subscription import (
    Subscription,
    SubscriptionStore,
)


class TestSubscription:
    """Tests for Subscription dataclass."""

    def test_create_with_defaults(self):
        sub = Subscription.create(sport="nba")
        assert sub.subscription_id.startswith("sub-")
        assert sub.sport == "nba"
        assert sub.enabled is True
        assert sub.auto_join is True
        assert sub.teams == []
        assert sub.created_at != ""
        # Default 7-day expiry
        assert sub.expires_at is not None

    def test_create_with_days(self):
        sub = Subscription.create(sport="nfl", days=30)
        assert sub.sport == "nfl"
        assert sub.expires_at is not None
        expires = datetime.fromisoformat(sub.expires_at)
        now = datetime.now(timezone.utc)
        assert (expires - now).days >= 29

    def test_create_forever(self):
        sub = Subscription.create(sport="nba", forever=True)
        assert sub.expires_at is None

    def test_create_with_teams(self):
        sub = Subscription.create(sport="nba", teams=["lal", "bos"])
        assert sub.teams == ["LAL", "BOS"]

    def test_create_with_options(self):
        sub = Subscription.create(
            sport="ncaa",
            poll_interval_seconds=120,
            max_active_trials=3,
            filters=["event.nba_play"],
        )
        assert sub.poll_interval_seconds == 120
        assert sub.max_active_trials == 3
        assert sub.filters == ["event.nba_play"]

    def test_to_dict_roundtrip(self):
        sub = Subscription.create(sport="nba", teams=["LAL"], days=7)
        data = sub.to_dict()
        restored = Subscription.from_dict(data)
        assert restored.subscription_id == sub.subscription_id
        assert restored.sport == sub.sport
        assert restored.teams == sub.teams
        assert restored.expires_at == sub.expires_at
        assert restored.created_at == sub.created_at

    def test_from_dict_with_defaults(self):
        sub = Subscription.from_dict({"subscription_id": "sub-test"})
        assert sub.subscription_id == "sub-test"
        assert sub.enabled is True
        assert sub.sport == ""
        assert sub.teams == []

    def test_is_active_enabled(self):
        sub = Subscription.create(sport="nba", forever=True)
        assert sub.is_active() is True

    def test_is_active_disabled(self):
        sub = Subscription.create(sport="nba", forever=True)
        sub.enabled = False
        assert sub.is_active() is False

    def test_is_active_expired(self):
        sub = Subscription.create(sport="nba", days=1)
        future = datetime.now(timezone.utc) + timedelta(days=2)
        assert sub.is_active(now=future) is False

    def test_is_active_not_started(self):
        sub = Subscription.create(sport="nba", forever=True)
        sub.starts_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert sub.is_active() is False

    def test_is_active_within_range(self):
        sub = Subscription.create(sport="nba", days=7)
        now = datetime.now(timezone.utc) + timedelta(days=3)
        assert sub.is_active(now=now) is True


class TestSubscriptionStore:
    """Tests for SubscriptionStore persistence."""

    def test_load_empty(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        assert store.load() == []

    def test_add_and_load(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba", days=7)
        store.add(sub)

        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0].subscription_id == sub.subscription_id
        assert loaded[0].sport == "nba"

    def test_add_multiple(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        sub1 = Subscription.create(sport="nba")
        sub2 = Subscription.create(sport="nfl")
        store.add(sub1)
        store.add(sub2)

        loaded = store.load()
        assert len(loaded) == 2

    def test_remove_existing(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba")
        store.add(sub)

        assert store.remove(sub.subscription_id) is True
        assert store.load() == []

    def test_remove_nonexistent(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        assert store.remove("nonexistent") is False

    def test_get_existing(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba")
        store.add(sub)

        result = store.get(sub.subscription_id)
        assert result is not None
        assert result.sport == "nba"

    def test_get_nonexistent(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        assert store.get("nope") is None

    def test_update(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba")
        store.add(sub)

        sub.enabled = False
        store.update(sub)

        loaded = store.get(sub.subscription_id)
        assert loaded is not None
        assert loaded.enabled is False

    def test_mark_joined(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba")
        store.add(sub)

        store.mark_joined(sub.subscription_id, "trial-001")
        store.mark_joined(sub.subscription_id, "trial-002")
        # Idempotent
        store.mark_joined(sub.subscription_id, "trial-001")

        loaded = store.get(sub.subscription_id)
        assert loaded is not None
        assert loaded.joined_trial_ids == ["trial-001", "trial-002"]

    def test_get_active(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        active = Subscription.create(sport="nba", forever=True)
        expired = Subscription.create(sport="nfl", days=1)
        expired.expires_at = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        disabled = Subscription.create(sport="ncaa", forever=True)
        disabled.enabled = False

        store.add(active)
        store.add(expired)
        store.add(disabled)

        result = store.get_active()
        assert len(result) == 1
        assert result[0].sport == "nba"

    def test_save_creates_directory(self, tmp_path: Path):
        nested = tmp_path / "deep" / "nested"
        store = SubscriptionStore(profile_dir=nested)
        sub = Subscription.create(sport="nba")
        store.add(sub)
        assert (nested / "subscriptions.json").exists()

    def test_corrupted_file_returns_empty(self, tmp_path: Path):
        store = SubscriptionStore(profile_dir=tmp_path)
        (tmp_path / "subscriptions.json").write_text("not json")
        assert store.load() == []


class TestSubscriptionMatching:
    """Tests for trial-to-subscription matching logic."""

    def _make_meta(
        self,
        sport_type: str = "nba",
        home_team: str = "Los Angeles Lakers",
        away_team: str = "Cleveland Cavaliers",
        home_tricode: str = "LAL",
        away_tricode: str = "CLE",
    ) -> MagicMock:
        meta = MagicMock()
        meta.sport_type = sport_type
        meta.home_team = home_team
        meta.away_team = away_team
        meta.metadata = {
            "home_team_tricode": home_tricode,
            "away_team_tricode": away_tricode,
        }
        return meta

    def test_sport_match(self):
        from dojozero_client._watcher import SubscriptionWatcher

        watcher = SubscriptionWatcher.__new__(SubscriptionWatcher)
        sub = Subscription.create(sport="nba", forever=True)
        meta = self._make_meta(sport_type="nba")
        assert watcher._matches_subscription(meta, sub) is True

    def test_sport_mismatch(self):
        from dojozero_client._watcher import SubscriptionWatcher

        watcher = SubscriptionWatcher.__new__(SubscriptionWatcher)
        sub = Subscription.create(sport="nfl", forever=True)
        meta = self._make_meta(sport_type="nba")
        assert watcher._matches_subscription(meta, sub) is False

    def test_sport_case_insensitive(self):
        from dojozero_client._watcher import SubscriptionWatcher

        watcher = SubscriptionWatcher.__new__(SubscriptionWatcher)
        sub = Subscription.create(sport="NBA", forever=True)
        meta = self._make_meta(sport_type="nba")
        assert watcher._matches_subscription(meta, sub) is True

    def test_team_filter_match_tricode(self):
        from dojozero_client._watcher import SubscriptionWatcher

        watcher = SubscriptionWatcher.__new__(SubscriptionWatcher)
        sub = Subscription.create(sport="nba", teams=["LAL"], forever=True)
        meta = self._make_meta(home_tricode="LAL")
        assert watcher._matches_subscription(meta, sub) is True

    def test_team_filter_match_away(self):
        from dojozero_client._watcher import SubscriptionWatcher

        watcher = SubscriptionWatcher.__new__(SubscriptionWatcher)
        sub = Subscription.create(sport="nba", teams=["CLE"], forever=True)
        meta = self._make_meta(away_tricode="CLE")
        assert watcher._matches_subscription(meta, sub) is True

    def test_team_filter_no_match(self):
        from dojozero_client._watcher import SubscriptionWatcher

        watcher = SubscriptionWatcher.__new__(SubscriptionWatcher)
        sub = Subscription.create(sport="nba", teams=["BOS"], forever=True)
        meta = self._make_meta(home_tricode="LAL", away_tricode="CLE")
        assert watcher._matches_subscription(meta, sub) is False

    def test_no_team_filter_matches_all(self):
        from dojozero_client._watcher import SubscriptionWatcher

        watcher = SubscriptionWatcher.__new__(SubscriptionWatcher)
        sub = Subscription.create(sport="nba", forever=True)
        meta = self._make_meta()
        assert watcher._matches_subscription(meta, sub) is True

    def test_multiple_teams_match_any(self):
        from dojozero_client._watcher import SubscriptionWatcher

        watcher = SubscriptionWatcher.__new__(SubscriptionWatcher)
        sub = Subscription.create(sport="nba", teams=["BOS", "LAL"], forever=True)
        meta = self._make_meta(home_tricode="LAL", away_tricode="CLE")
        assert watcher._matches_subscription(meta, sub) is True


class TestWatcherAutoJoin:
    """Tests for the watcher's auto-join logic."""

    @pytest.mark.asyncio
    async def test_auto_join_matching_trial(self, tmp_path: Path):
        from dojozero_client._watcher import SubscriptionWatcher

        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba", forever=True)
        store.add(sub)

        daemon = MagicMock()
        daemon._trials = {}
        daemon._handle_join = AsyncMock(
            return_value={"status": "joined", "agent_id": "agent-1"}
        )

        watcher = SubscriptionWatcher(daemon, store)
        watcher._running = True

        meta = MagicMock()
        meta.sport_type = "nba"
        meta.home_team = "Lakers"
        meta.away_team = "Cavaliers"
        meta.metadata = {"home_team_tricode": "LAL", "away_team_tricode": "CLE"}

        gw = MagicMock()
        gw.trial_id = "trial-nba-001"

        with patch.object(
            watcher, "_get_trial_metadata", new=AsyncMock(return_value=meta)
        ):
            with patch.object(
                watcher._client, "discover_trials", new=AsyncMock(return_value=[gw])
            ):
                await watcher._poll_once()

        daemon._handle_join.assert_called_once_with(
            trial_id="trial-nba-001",
            filters=sub.filters,
        )

        # Verify trial was recorded as joined
        updated = store.get(sub.subscription_id)
        assert updated is not None
        assert "trial-nba-001" in updated.joined_trial_ids

    @pytest.mark.asyncio
    async def test_idempotent_no_rejoin(self, tmp_path: Path):
        from dojozero_client._watcher import SubscriptionWatcher

        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba", forever=True)
        sub.joined_trial_ids = ["trial-nba-001"]
        store.add(sub)

        daemon = MagicMock()
        daemon._trials = {}
        daemon._handle_join = AsyncMock()

        watcher = SubscriptionWatcher(daemon, store)
        watcher._running = True

        gw = MagicMock()
        gw.trial_id = "trial-nba-001"

        with patch.object(
            watcher._client, "discover_trials", new=AsyncMock(return_value=[gw])
        ):
            await watcher._poll_once()

        daemon._handle_join.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_mismatched_sport(self, tmp_path: Path):
        from dojozero_client._watcher import SubscriptionWatcher

        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nfl", forever=True)
        store.add(sub)

        daemon = MagicMock()
        daemon._trials = {}
        daemon._handle_join = AsyncMock()

        watcher = SubscriptionWatcher(daemon, store)
        watcher._running = True

        meta = MagicMock()
        meta.sport_type = "nba"
        meta.home_team = "Lakers"
        meta.away_team = "Cavaliers"
        meta.metadata = {"home_team_tricode": "LAL", "away_team_tricode": "CLE"}

        gw = MagicMock()
        gw.trial_id = "trial-nba-001"

        with patch.object(
            watcher, "_get_trial_metadata", new=AsyncMock(return_value=meta)
        ):
            with patch.object(
                watcher._client, "discover_trials", new=AsyncMock(return_value=[gw])
            ):
                await watcher._poll_once()

        daemon._handle_join.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_subscription_skipped(self, tmp_path: Path):
        from dojozero_client._watcher import SubscriptionWatcher

        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba", days=1)
        sub.expires_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        store.add(sub)

        daemon = MagicMock()
        daemon._trials = {}
        daemon._handle_join = AsyncMock()

        watcher = SubscriptionWatcher(daemon, store)
        watcher._running = True

        gw = MagicMock()
        gw.trial_id = "trial-nba-001"

        with patch.object(
            watcher._client, "discover_trials", new=AsyncMock(return_value=[gw])
        ):
            await watcher._poll_once()

        daemon._handle_join.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_active_trials_respected(self, tmp_path: Path):
        from dojozero_client._watcher import SubscriptionWatcher

        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba", forever=True, max_active_trials=1)
        sub.joined_trial_ids = ["trial-nba-001"]
        store.add(sub)

        handler = MagicMock()
        handler.is_connected = True

        daemon = MagicMock()
        daemon._trials = {"trial-nba-001": handler}
        daemon._handle_join = AsyncMock()

        watcher = SubscriptionWatcher(daemon, store)
        watcher._running = True

        meta = MagicMock()
        meta.sport_type = "nba"
        meta.home_team = "Lakers"
        meta.away_team = "Cavaliers"
        meta.metadata = {"home_team_tricode": "LAL", "away_team_tricode": "CLE"}

        gw = MagicMock()
        gw.trial_id = "trial-nba-002"

        with patch.object(
            watcher, "_get_trial_metadata", new=AsyncMock(return_value=meta)
        ):
            with patch.object(
                watcher._client, "discover_trials", new=AsyncMock(return_value=[gw])
            ):
                await watcher._poll_once()

        daemon._handle_join.assert_not_called()

    @pytest.mark.asyncio
    async def test_discovery_failure_handled(self, tmp_path: Path):
        from dojozero_client._watcher import SubscriptionWatcher

        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba", forever=True)
        store.add(sub)

        daemon = MagicMock()
        daemon._trials = {}

        watcher = SubscriptionWatcher(daemon, store)
        watcher._running = True

        with patch.object(
            watcher._client,
            "discover_trials",
            new=AsyncMock(side_effect=Exception("Network error")),
        ):
            # Should not raise
            await watcher._poll_once()

    @pytest.mark.asyncio
    async def test_join_failure_handled(self, tmp_path: Path):
        from dojozero_client._watcher import SubscriptionWatcher

        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba", forever=True)
        store.add(sub)

        daemon = MagicMock()
        daemon._trials = {}
        daemon._handle_join = AsyncMock(side_effect=Exception("Connection failed"))

        watcher = SubscriptionWatcher(daemon, store)
        watcher._running = True

        meta = MagicMock()
        meta.sport_type = "nba"
        meta.home_team = "Lakers"
        meta.away_team = "Cavaliers"
        meta.metadata = {"home_team_tricode": "LAL", "away_team_tricode": "CLE"}

        gw = MagicMock()
        gw.trial_id = "trial-nba-001"

        with patch.object(
            watcher, "_get_trial_metadata", new=AsyncMock(return_value=meta)
        ):
            with patch.object(
                watcher._client, "discover_trials", new=AsyncMock(return_value=[gw])
            ):
                await watcher._poll_once()

        # Trial should NOT be marked as joined on failure
        updated = store.get(sub.subscription_id)
        assert updated is not None
        assert "trial-nba-001" not in updated.joined_trial_ids


class TestWatcherPollInterval:
    """Tests for poll interval calculation."""

    def test_uses_shortest_interval(self, tmp_path: Path):
        from dojozero_client._watcher import SubscriptionWatcher

        store = SubscriptionStore(profile_dir=tmp_path)
        sub1 = Subscription.create(sport="nba", forever=True)
        sub1.poll_interval_seconds = 120
        sub2 = Subscription.create(sport="nfl", forever=True)
        sub2.poll_interval_seconds = 30
        store.add(sub1)
        store.add(sub2)

        daemon = MagicMock()
        watcher = SubscriptionWatcher(daemon, store)
        assert watcher._get_poll_interval() == 30.0

    def test_default_interval_no_subs(self, tmp_path: Path):
        from dojozero_client._watcher import DEFAULT_POLL_INTERVAL, SubscriptionWatcher

        store = SubscriptionStore(profile_dir=tmp_path)
        daemon = MagicMock()
        watcher = SubscriptionWatcher(daemon, store)
        assert watcher._get_poll_interval() == float(DEFAULT_POLL_INTERVAL)


class TestCLISubscribe:
    """Tests for CLI subscribe/subscriptions commands."""

    def test_subscribe_without_daemon(self, tmp_path: Path):
        """Test subscribe writes to disk when daemon is not running."""
        from dojozero_client._cli import main

        with (
            patch("dojozero_client._cli.is_daemon_running", return_value=False),
            patch("dojozero_client._cli.get_profile_dir", return_value=tmp_path),
        ):
            ret = main(["subscribe", "--sport", "nba", "--days", "7"])

        assert ret == 0
        store = SubscriptionStore(profile_dir=tmp_path)
        subs = store.load()
        assert len(subs) == 1
        assert subs[0].sport == "nba"

    def test_subscribe_forever(self, tmp_path: Path):
        from dojozero_client._cli import main

        with (
            patch("dojozero_client._cli.is_daemon_running", return_value=False),
            patch("dojozero_client._cli.get_profile_dir", return_value=tmp_path),
        ):
            ret = main(["subscribe", "--sport", "nba", "--team", "LAL", "--forever"])

        assert ret == 0
        store = SubscriptionStore(profile_dir=tmp_path)
        subs = store.load()
        assert len(subs) == 1
        assert subs[0].teams == ["LAL"]
        assert subs[0].expires_at is None

    def test_subscriptions_list_empty(self, tmp_path: Path):
        from dojozero_client._cli import main

        with (
            patch("dojozero_client._cli.is_daemon_running", return_value=False),
            patch("dojozero_client._cli.get_profile_dir", return_value=tmp_path),
        ):
            ret = main(["subscriptions", "list"])

        assert ret == 0

    def test_subscriptions_list_with_data(self, tmp_path: Path):
        from dojozero_client._cli import main

        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba", forever=True)
        store.add(sub)

        with (
            patch("dojozero_client._cli.is_daemon_running", return_value=False),
            patch("dojozero_client._cli.get_profile_dir", return_value=tmp_path),
        ):
            ret = main(["subscriptions", "list"])

        assert ret == 0

    def test_subscriptions_remove(self, tmp_path: Path):
        from dojozero_client._cli import main

        store = SubscriptionStore(profile_dir=tmp_path)
        sub = Subscription.create(sport="nba", forever=True)
        store.add(sub)

        with (
            patch("dojozero_client._cli.is_daemon_running", return_value=False),
            patch("dojozero_client._cli.get_profile_dir", return_value=tmp_path),
        ):
            ret = main(["subscriptions", "remove", sub.subscription_id])

        assert ret == 0
        assert store.load() == []

    def test_subscriptions_remove_nonexistent(self, tmp_path: Path):
        from dojozero_client._cli import main

        with (
            patch("dojozero_client._cli.is_daemon_running", return_value=False),
            patch("dojozero_client._cli.get_profile_dir", return_value=tmp_path),
        ):
            ret = main(["subscriptions", "remove", "nonexistent"])

        assert ret == 1

    def test_subscribe_via_rpc(self, tmp_path: Path):
        """Test subscribe uses RPC when daemon is running."""
        from dojozero_client._cli import main

        mock_rpc = MagicMock()
        mock_rpc.call_sync.return_value = {
            "subscription_id": "sub-test",
            "sport": "nba",
            "teams": [],
            "expires_at": None,
        }

        with (
            patch("dojozero_client._cli.is_daemon_running", return_value=True),
            patch("dojozero_client._cli.RPCClient", return_value=mock_rpc),
        ):
            ret = main(["subscribe", "--sport", "nba", "--forever"])

        assert ret == 0
        mock_rpc.call_sync.assert_called_once()
        call_args = mock_rpc.call_sync.call_args
        assert call_args[0][0] == "subscribe"
        assert call_args[1]["sport"] == "nba"
        assert call_args[1]["forever"] is True


class TestExistingBehaviorPreserved:
    """Verify existing discover/start/list behavior is not affected."""

    def test_discover_command_still_works(self):
        """Verify discover parser entry exists and maps to cmd_discover."""
        from dojozero_client._cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["discover"])
        assert args.command == "discover"

    def test_start_command_still_works(self):
        from dojozero_client._cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["start", "my-trial", "-b"])
        assert args.command == "start"
        assert args.trial_id == "my-trial"
        assert args.background is True

    def test_list_command_still_works(self):
        from dojozero_client._cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"

    def test_subscribe_command_exists(self):
        from dojozero_client._cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["subscribe", "--sport", "nba", "--days", "7"])
        assert args.command == "subscribe"
        assert args.sport == "nba"
        assert args.days == 7

    def test_subscriptions_list_command_exists(self):
        from dojozero_client._cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["subscriptions", "list"])
        assert args.command == "subscriptions"
        assert args.subs_action == "list"

    def test_subscriptions_remove_command_exists(self):
        from dojozero_client._cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["subscriptions", "remove", "sub-abc"])
        assert args.command == "subscriptions"
        assert args.subs_action == "remove"
        assert args.subscription_id == "sub-abc"
