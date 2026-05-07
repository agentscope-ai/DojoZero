"""Tests for _resolve_event_files HTTP polling (202 materialization flow).

Exercises the client side of the async SLS materialization contract:

- 200 on first attempt: writes cache and returns immediately
- 202 -> 200: polls with exponential backoff, capped at 15s, then writes cache
- 202 with stalled progress: raises DojoZeroCLIError after max_stalls
- Non-2xx, non-202 response: raises DojoZeroCLIError (via raise_for_status)
- Progress log formatting: "?" and no percentage when spans_total is 0
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from dojozero.cli import DojoZeroCLIError, _resolve_event_files


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fake httpx.AsyncClient / Response
# ---------------------------------------------------------------------------


class _FakeHttpResponse:
    def __init__(
        self,
        *,
        status_code: int,
        content: bytes = b"",
        json_body: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self._json = json_body or {}

    def json(self) -> dict[str, Any]:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            req = httpx.Request("GET", "http://fake")
            resp = httpx.Response(self.status_code, request=req)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=req, response=resp
            )


class _FakeHttpClient:
    """Both the AsyncClient class and instance — async context-manager enabled."""

    def __init__(self, responses: list[_FakeHttpResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.call_log: list[str] = []

    def __call__(self, *args: Any, **kwargs: Any) -> "_FakeHttpClient":
        del args, kwargs
        return self

    async def __aenter__(self) -> "_FakeHttpClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args
        return None

    async def get(self, url: str) -> _FakeHttpResponse:
        self.call_log.append(url)
        # Saturate on the last response so overly-long tests surface as a
        # stalled loop (caught by stall detection) rather than IndexError.
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fast_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace asyncio.sleep with a no-op that records requested delays."""
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return delays


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, responses: list[_FakeHttpResponse]
) -> _FakeHttpClient:
    fake = _FakeHttpClient(responses)
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    return fake


# ---------------------------------------------------------------------------
# 200 on first attempt — no polling
# ---------------------------------------------------------------------------


def test_http_url_200_on_first_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_sleep: list[float]
) -> None:
    payload = b'{"event_type": "event.game_initialize"}\n'
    fake = _install_fake_client(
        monkeypatch, [_FakeHttpResponse(status_code=200, content=payload)]
    )

    files = _run(
        _resolve_event_files(
            ["http://server/api/trials/trial-A/events.jsonl"],
            sls_cache_dir=tmp_path,
        )
    )

    assert len(files) == 1
    assert files[0].read_bytes() == payload
    assert len(fake.call_log) == 1
    # Success on first attempt — no polling, no sleeps.
    assert fast_sleep == []


# ---------------------------------------------------------------------------
# 202 -> 200 happy path with backoff
# ---------------------------------------------------------------------------


def test_http_url_polls_until_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_sleep: list[float]
) -> None:
    payload = b'{"event_type": "event.game_initialize"}\n'
    _install_fake_client(
        monkeypatch,
        [
            _FakeHttpResponse(
                status_code=202,
                json_body={"spans_fetched": 0, "spans_total": 0},
            ),
            _FakeHttpResponse(
                status_code=202,
                json_body={"spans_fetched": 10, "spans_total": 100},
            ),
            _FakeHttpResponse(
                status_code=202,
                json_body={"spans_fetched": 50, "spans_total": 100},
            ),
            _FakeHttpResponse(status_code=200, content=payload),
        ],
    )

    files = _run(
        _resolve_event_files(
            ["http://server/api/trials/trial-A/events.jsonl"],
            sls_cache_dir=tmp_path,
        )
    )

    assert files[0].read_bytes() == payload
    # Three 202 responses → three sleeps with geometric backoff (3.0, 4.5, 6.75).
    assert fast_sleep == pytest.approx([3.0, 4.5, 6.75])


# ---------------------------------------------------------------------------
# Backoff caps at 15s
# ---------------------------------------------------------------------------


def test_http_url_backoff_caps_at_15_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_sleep: list[float]
) -> None:
    # Each 202 reports growing progress so stall detection stays reset.
    responses: list[_FakeHttpResponse] = [
        _FakeHttpResponse(
            status_code=202,
            json_body={"spans_fetched": i, "spans_total": 100},
        )
        for i in range(12)
    ]
    responses.append(_FakeHttpResponse(status_code=200, content=b""))
    _install_fake_client(monkeypatch, responses)

    _run(
        _resolve_event_files(
            ["http://server/api/trials/trial-A/events.jsonl"],
            sls_cache_dir=tmp_path,
        )
    )

    # Backoff grows 3.0 * 1.5^n until capped at 15.0; verify plateau.
    assert 15.0 in fast_sleep
    assert max(fast_sleep) == 15.0


# ---------------------------------------------------------------------------
# Stall detection: 20 consecutive 202s with no new spans → DojoZeroCLIError
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fast_sleep")
def test_http_url_stall_detection_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # spans_fetched=5 throughout. First 202 counts as "initial progress" since
    # last_spans starts at -1; subsequent 20 without growth hit max_stalls=20.
    stalled = _FakeHttpResponse(
        status_code=202,
        json_body={"spans_fetched": 5, "spans_total": 100},
    )
    _install_fake_client(monkeypatch, [stalled] * 25)

    with pytest.raises(DojoZeroCLIError, match="stalled"):
        _run(
            _resolve_event_files(
                ["http://server/api/trials/trial-A/events.jsonl"],
                sls_cache_dir=tmp_path,
            )
        )


# ---------------------------------------------------------------------------
# Unexpected non-2xx status → DojoZeroCLIError via raise_for_status
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fast_sleep")
def test_http_url_unexpected_status_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(monkeypatch, [_FakeHttpResponse(status_code=500)])

    with pytest.raises(DojoZeroCLIError, match="HTTP 500"):
        _run(
            _resolve_event_files(
                ["http://server/api/trials/trial-A/events.jsonl"],
                sls_cache_dir=tmp_path,
            )
        )


# ---------------------------------------------------------------------------
# Progress log formatting — percentage omitted when total == 0
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fast_sleep")
def test_http_url_progress_log_handles_zero_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install_fake_client(
        monkeypatch,
        [
            _FakeHttpResponse(
                status_code=202,
                json_body={"spans_fetched": 5, "spans_total": 0},
            ),
            _FakeHttpResponse(status_code=200, content=b""),
        ],
    )

    with caplog.at_level("INFO", logger="dojozero.cli"):
        _run(
            _resolve_event_files(
                ["http://server/api/trials/trial-A/events.jsonl"],
                sls_cache_dir=tmp_path,
            )
        )

    materializing_msgs = [
        r.getMessage() for r in caplog.records if "materializing" in r.getMessage()
    ]
    assert materializing_msgs, "expected at least one materialization log line"
    msg = materializing_msgs[0]
    # "?" placeholder for total and no percentage when total == 0.
    assert "5/?" in msg
    assert "%" not in msg


@pytest.mark.usefixtures("fast_sleep")
def test_http_url_progress_log_shows_percent_when_total_known(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install_fake_client(
        monkeypatch,
        [
            _FakeHttpResponse(
                status_code=202,
                json_body={"spans_fetched": 25, "spans_total": 100},
            ),
            _FakeHttpResponse(status_code=200, content=b""),
        ],
    )

    with caplog.at_level("INFO", logger="dojozero.cli"):
        _run(
            _resolve_event_files(
                ["http://server/api/trials/trial-A/events.jsonl"],
                sls_cache_dir=tmp_path,
            )
        )

    msg = next(
        r.getMessage() for r in caplog.records if "materializing" in r.getMessage()
    )
    assert "25/100" in msg
    assert "25%" in msg
