"""Tests for arena dev --span-start parsing."""

from datetime import datetime, timezone

import pytest

from dojozero.cli import DojoZeroCLIError, _parse_dev_span_start


def test_parse_dev_span_start_date() -> None:
    dt = _parse_dev_span_start("2026-04-08")
    assert dt == datetime(2026, 4, 8, tzinfo=timezone.utc)


def test_parse_dev_span_start_none() -> None:
    assert _parse_dev_span_start(None) is None
    assert _parse_dev_span_start("") is None
    assert _parse_dev_span_start("   ") is None


def test_parse_dev_span_start_invalid() -> None:
    with pytest.raises(DojoZeroCLIError):
        _parse_dev_span_start("not-a-date")
