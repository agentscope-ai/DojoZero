"""Tests for DOJOZERO_MAX_DAILY_GAMES (serve trial source override)."""

import pytest

from dojozero.cli import DojoZeroCLIError, _parse_max_daily_games_env_override


class TestParseMaxDailyGamesEnvOverride:
    def test_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DOJOZERO_MAX_DAILY_GAMES", raising=False)
        assert _parse_max_daily_games_env_override() is None

    def test_empty_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOJOZERO_MAX_DAILY_GAMES", "  ")
        assert _parse_max_daily_games_env_override() is None

    def test_zero_unlimited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOJOZERO_MAX_DAILY_GAMES", "0")
        assert _parse_max_daily_games_env_override() == 0

    def test_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOJOZERO_MAX_DAILY_GAMES", "3")
        assert _parse_max_daily_games_env_override() == 3

    def test_invalid_not_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOJOZERO_MAX_DAILY_GAMES", "x")
        with pytest.raises(DojoZeroCLIError, match="integer"):
            _parse_max_daily_games_env_override()

    def test_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOJOZERO_MAX_DAILY_GAMES", "-1")
        with pytest.raises(DojoZeroCLIError, match=">="):
            _parse_max_daily_games_env_override()
