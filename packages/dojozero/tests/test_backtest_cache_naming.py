"""Tests for trial cache filename scheme and symlink helpers.

The server and CLI use a canonical ``outputs/`` directory:
  {trial_id}.jsonl              (no run_id)
  {trial_id}-{run_id[:8]}.jsonl (with run_id)

8-char prefix of the 16-hex span_id keeps filenames readable while
avoiding collisions.
"""

import os
from pathlib import Path

from dojozero.dashboard_server._trial_manager import (
    _legacy_trials_cache_path,
    ensure_trial_symlink,
    trials_cache_path,
)


class TestTrialsCachePath:
    """Verify trials_cache_path produces expected paths."""

    def test_no_run_id(self, tmp_path: Path) -> None:
        result = trials_cache_path(tmp_path, "abc123")
        assert result == tmp_path / "abc123.jsonl"

    def test_empty_run_id(self, tmp_path: Path) -> None:
        result = trials_cache_path(tmp_path, "abc123", "")
        assert result == tmp_path / "abc123.jsonl"

    def test_with_run_id(self, tmp_path: Path) -> None:
        result = trials_cache_path(tmp_path, "trial-1", "abcdef1234567890")
        assert result == tmp_path / "trial-1-abcdef12.jsonl"

    def test_short_run_id(self, tmp_path: Path) -> None:
        result = trials_cache_path(tmp_path, "trial-1", "abc")
        assert result == tmp_path / "trial-1-abc.jsonl"

    def test_different_run_ids(self, tmp_path: Path) -> None:
        p1 = trials_cache_path(tmp_path, "trial-1", "run_aaa_1234")
        p2 = trials_cache_path(tmp_path, "trial-1", "run_bbb_5678")
        assert p1 != p2


class TestLegacyTrialsCachePath:
    """Verify _legacy_trials_cache_path still produces old-style paths."""

    def test_no_run_id(self, tmp_path: Path) -> None:
        result = _legacy_trials_cache_path(tmp_path, "abc123")
        assert result == tmp_path / "trials" / "abc123.jsonl"

    def test_with_run_id(self, tmp_path: Path) -> None:
        result = _legacy_trials_cache_path(tmp_path, "trial-1", "abcdef1234567890")
        assert result == tmp_path / "trials" / "trial-1-abcdef12.jsonl"


class TestCacheHitMiss:
    """Verify cache reuse behavior (file exists -> skip fetch)."""

    def test_cache_hit_skips_materialization(self, tmp_path: Path) -> None:
        """When cache file exists, no SLS fetch should be needed."""
        p = trials_cache_path(tmp_path, "trial-xyz", "run12345678abcdef")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"event": "test"}\n')
        assert p.exists()

    def test_cache_miss_for_different_run_id(self, tmp_path: Path) -> None:
        """A cached file for run_A should NOT satisfy a request for run_B."""
        p_a = trials_cache_path(tmp_path, "trial-xyz", "run_aaaa_xxx")
        p_a.parent.mkdir(parents=True, exist_ok=True)
        p_a.write_text('{"event": "test"}\n')

        p_b = trials_cache_path(tmp_path, "trial-xyz", "run_bbbb_xxx")
        assert not p_b.exists()

    def test_no_run_id_does_not_match_run_id_file(self, tmp_path: Path) -> None:
        """A request with no run_id should not reuse a run_id-specific cache."""
        specific = trials_cache_path(tmp_path, "trial-xyz", "run_aaaa_xxx")
        specific.parent.mkdir(parents=True, exist_ok=True)
        specific.write_text('{"event": "test"}\n')

        plain = trials_cache_path(tmp_path, "trial-xyz")
        assert not plain.exists()


class TestEnsureTrialSymlink:
    """Tests for ensure_trial_symlink helper (legacy, kept for compatibility)."""

    def test_creates_symlink(self, tmp_path: Path) -> None:
        persistence = tmp_path / "2026-04-21" / "sched-nba-123.jsonl"
        persistence.parent.mkdir(parents=True)
        persistence.write_text('{"event": "data"}\n')

        ensure_trial_symlink(tmp_path, "nba-game-123-abcd1234", persistence)

        link = tmp_path / "trials" / "nba-game-123-abcd1234.jsonl"
        assert link.is_symlink()
        assert link.exists()  # follows symlink
        assert link.read_text() == '{"event": "data"}\n'

    def test_uses_relative_path(self, tmp_path: Path) -> None:
        persistence = tmp_path / "2026-04-21" / "sched-nba-123.jsonl"
        persistence.parent.mkdir(parents=True)
        persistence.write_text("data")

        ensure_trial_symlink(tmp_path, "trial-1", persistence)

        link = tmp_path / "trials" / "trial-1.jsonl"
        target = os.readlink(str(link))
        assert not os.path.isabs(target)
        assert target == "../2026-04-21/sched-nba-123.jsonl"

    def test_idempotent(self, tmp_path: Path) -> None:
        persistence = tmp_path / "data" / "file.jsonl"
        persistence.parent.mkdir(parents=True)
        persistence.write_text("data")

        ensure_trial_symlink(tmp_path, "trial-1", persistence)
        ensure_trial_symlink(tmp_path, "trial-1", persistence)

        link = tmp_path / "trials" / "trial-1.jsonl"
        assert link.is_symlink()
        assert link.exists()

    def test_does_not_clobber_real_file(self, tmp_path: Path) -> None:
        """If a real file exists (e.g. from OSS download), don't replace it."""
        trials_dir = tmp_path / "trials"
        trials_dir.mkdir(parents=True)
        real_file = trials_dir / "trial-1.jsonl"
        real_file.write_text("oss data")

        persistence = tmp_path / "data" / "file.jsonl"
        persistence.parent.mkdir(parents=True)
        persistence.write_text("live data")

        ensure_trial_symlink(tmp_path, "trial-1", persistence)

        assert not real_file.is_symlink()
        assert real_file.read_text() == "oss data"

    def test_replaces_stale_symlink(self, tmp_path: Path) -> None:
        """If symlink points to old target, update it."""
        old_persistence = tmp_path / "old" / "file.jsonl"
        old_persistence.parent.mkdir(parents=True)
        old_persistence.write_text("old")

        ensure_trial_symlink(tmp_path, "trial-1", old_persistence)

        new_persistence = tmp_path / "new" / "file.jsonl"
        new_persistence.parent.mkdir(parents=True)
        new_persistence.write_text("new")

        ensure_trial_symlink(tmp_path, "trial-1", new_persistence)

        link = tmp_path / "trials" / "trial-1.jsonl"
        assert os.readlink(str(link)) == "../new/file.jsonl"
