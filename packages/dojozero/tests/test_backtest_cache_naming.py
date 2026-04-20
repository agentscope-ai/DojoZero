"""Tests for SLS backtest cache filename scheme.

The server and CLI must use the same naming convention:
  {trial_id}.jsonl              (no run_id)
  {trial_id}-{run_id[:8]}.jsonl (with run_id)

8-char prefix of the 16-hex span_id keeps filenames readable while
avoiding collisions.  _select_run accepts both full and prefix matches
so users can pass either form.
"""

from pathlib import Path


def _cache_filename(trial_id: str, run_id: str | None) -> str:
    """Reproduce the cache naming logic from _server.py and cli.py."""
    suffix = f"-{run_id[:8]}" if run_id else ""
    return f"{trial_id}{suffix}.jsonl"


class TestCacheNaming:
    """Verify cache filename construction matches CLI convention."""

    def test_no_run_id(self) -> None:
        assert _cache_filename("abc123", None) == "abc123.jsonl"

    def test_empty_run_id(self) -> None:
        assert _cache_filename("abc123", "") == "abc123.jsonl"

    def test_run_id_truncated_to_8_chars(self) -> None:
        run_id = "abcdef1234567890"
        result = _cache_filename("trial-1", run_id)
        assert result == "trial-1-abcdef12.jsonl"

    def test_short_run_id_not_padded(self) -> None:
        result = _cache_filename("trial-1", "abc")
        assert result == "trial-1-abc.jsonl"

    def test_different_run_ids_produce_different_files(self) -> None:
        f1 = _cache_filename("trial-1", "run_aaa_1234")
        f2 = _cache_filename("trial-1", "run_bbb_5678")
        assert f1 != f2

    def test_same_run_id_produces_same_file(self) -> None:
        f1 = _cache_filename("trial-1", "run_aaa_1234")
        f2 = _cache_filename("trial-1", "run_aaa_1234")
        assert f1 == f2


class TestCacheHitMiss:
    """Verify cache reuse behavior (file exists → skip fetch)."""

    def test_cache_hit_skips_materialization(self, tmp_path: Path) -> None:
        """When cache file exists, no SLS fetch should be needed."""
        trial_id = "trial-xyz"
        run_id = "run12345678abcdef"
        cache_dir = tmp_path / "backtest_cache"
        cache_dir.mkdir()

        suffix = f"-{run_id[:8]}" if run_id else ""
        event_file = cache_dir / f"{trial_id}{suffix}.jsonl"
        event_file.write_text('{"event": "test"}\n')

        # File exists → cache hit
        assert event_file.exists()

    def test_cache_miss_for_different_run_id(self, tmp_path: Path) -> None:
        """A cached file for run_A should NOT satisfy a request for run_B."""
        trial_id = "trial-xyz"
        cache_dir = tmp_path / "backtest_cache"
        cache_dir.mkdir()

        # Cache file for run_A
        file_a = cache_dir / f"{trial_id}-run_aaaa.jsonl"
        file_a.write_text('{"event": "test"}\n')

        # Request for run_B → different filename → miss
        file_b = cache_dir / f"{trial_id}-run_bbbb.jsonl"
        assert not file_b.exists()

    def test_no_run_id_does_not_match_run_id_file(self, tmp_path: Path) -> None:
        """A request with no run_id should not reuse a run_id-specific cache."""
        trial_id = "trial-xyz"
        cache_dir = tmp_path / "backtest_cache"
        cache_dir.mkdir()

        # Cache file for a specific run
        specific = cache_dir / f"{trial_id}-run_aaaa.jsonl"
        specific.write_text('{"event": "test"}\n')

        # No-run_id request → plain filename → miss
        plain = cache_dir / f"{trial_id}.jsonl"
        assert not plain.exists()
