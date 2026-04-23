"""Tests for SLSTraceReader._get_count and progress_callback in get_spans.

These paths were added to support async 202-polling of SLS materialization so
the dashboard server can report progress to the CLI client. Both the count
query and the per-page progress callback are new surface area.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from dojozero.core._credentials import Credentials
from dojozero.core._tracing import SLSTraceReader


def _run(coro):
    return asyncio.run(coro)


class _DummyCredentialProvider:
    """Returns fixed, non-empty credentials without any network lookup.

    The real CredentialProvider delegates to the alibabacloud SDK, which
    probes ECS metadata at 100.100.100.200 when no local credentials are
    configured — fine locally, but in CI this hangs for ~60s then fails.
    """

    def get_credentials(self) -> Credentials:
        return Credentials(access_key_id="test-ak", access_key_secret="test-secret")


@pytest.fixture(autouse=True)
def _stub_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub credential lookup for every test in this module.

    SLSTraceReader.__init__ calls ``get_credential_provider().get_credentials()``.
    Without this fixture, that triggers the alibabacloud SDK's full credential
    chain — including an ECS metadata probe that times out in CI.
    """
    from dojozero.core import _credentials as cred_mod

    monkeypatch.setattr(cred_mod, "_provider", None)
    monkeypatch.setattr(
        cred_mod, "get_credential_provider", lambda: _DummyCredentialProvider()
    )


def _make_reader(page_size: int = 100) -> SLSTraceReader:
    """Construct a reader with a stubbed credential provider.

    The ``_stub_credentials`` autouse fixture must be active — otherwise
    SLSTraceReader.__init__ will probe ECS metadata on hosts without local
    Alibaba Cloud credentials configured.
    """
    return SLSTraceReader(
        endpoint="fake.log.aliyuncs.com",
        project="fake-project",
        logstore="fake-store",
        page_size=page_size,
    )


class _FakeResponse:
    """Minimal httpx.Response stand-in sufficient for SLSTraceReader paths."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: Any = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.headers = headers or {"x-log-progress": "Complete"}

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            req = httpx.Request("GET", "http://fake")
            resp = httpx.Response(self.status_code, request=req)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=req, response=resp
            )


def _from_time() -> datetime:
    return datetime(2026, 4, 1, tzinfo=timezone.utc)


def _to_time() -> datetime:
    return datetime(2026, 4, 2, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _get_count
# ---------------------------------------------------------------------------


def test_get_count_parses_list_response() -> None:
    reader = _make_reader()
    reader._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_FakeResponse(json_body=[{"cnt": "1234"}])
    )

    count = _run(
        reader._get_count(
            resource="/logstores/fake",
            url="http://fake",
            query='_service:"dojozero" AND _trace_id:"trial-A"',
            from_time=_from_time(),
            to_time=_to_time(),
        )
    )

    assert count == 1234
    # Query is extended with a SQL count aggregation.
    call_kwargs = reader._client.get.call_args.kwargs
    assert call_kwargs["params"]["query"].endswith("| select count(1) as cnt")


def test_get_count_parses_dict_response() -> None:
    reader = _make_reader()
    reader._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_FakeResponse(json_body={"data": [{"cnt": 42}]})
    )

    count = _run(
        reader._get_count(
            resource="/logstores/fake",
            url="http://fake",
            query="q",
            from_time=_from_time(),
            to_time=_to_time(),
        )
    )

    assert count == 42


def test_get_count_returns_zero_on_empty_rows() -> None:
    reader = _make_reader()
    reader._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_FakeResponse(json_body=[])
    )

    count = _run(
        reader._get_count(
            resource="/logstores/fake",
            url="http://fake",
            query="q",
            from_time=_from_time(),
            to_time=_to_time(),
        )
    )

    assert count == 0


def test_get_count_returns_zero_on_http_error() -> None:
    reader = _make_reader()
    reader._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_FakeResponse(status_code=500, text="boom")
    )

    count = _run(
        reader._get_count(
            resource="/logstores/fake",
            url="http://fake",
            query="q",
            from_time=_from_time(),
            to_time=_to_time(),
        )
    )

    # Failure is non-fatal — returns 0 so materialization continues.
    assert count == 0


def test_get_count_returns_zero_on_transport_error() -> None:
    reader = _make_reader()
    reader._client.get = AsyncMock(  # type: ignore[method-assign]
        side_effect=httpx.ConnectError("boom"),
    )

    count = _run(
        reader._get_count(
            resource="/logstores/fake",
            url="http://fake",
            query="q",
            from_time=_from_time(),
            to_time=_to_time(),
        )
    )

    assert count == 0


# ---------------------------------------------------------------------------
# get_spans(progress_callback=...)
# ---------------------------------------------------------------------------


def _row(span_id: str) -> dict[str, Any]:
    """SLS row shape — only the fields downstream code reads matter here.

    We want rows to be *counted* in `all_rows`; whether they later convert
    into SpanData doesn't affect progress-callback firing, which happens
    on pagination boundaries before conversion.
    """
    return {
        "_trace_id": "trial-A",
        "_span_id": span_id,
        "_operation_name": "event.test",
        "_start_time": "1700000000000000",
        "_duration": "0",
    }


def _scripted_get(responses: list[_FakeResponse]) -> AsyncMock:
    """AsyncMock that yields responses in order, one per call.

    Tests are expected to exhaust exactly the number of responses provided
    — extra calls raise StopIteration, which surfaces as a test failure.
    """
    return AsyncMock(side_effect=list(responses))


def test_get_spans_calls_progress_callback_per_page() -> None:
    """Each pagination response triggers a progress call with (fetched, total)."""
    reader = _make_reader(page_size=2)

    # Count query answers 5 total, then three pages of rows: 2, 2, 1.
    reader._client.get = _scripted_get(  # type: ignore[method-assign]
        [
            _FakeResponse(json_body=[{"cnt": "5"}]),  # count query
            _FakeResponse(json_body=[_row("a"), _row("b")]),
            _FakeResponse(json_body=[_row("c"), _row("d")]),
            _FakeResponse(json_body=[_row("e")]),
        ]
    )

    progress: list[tuple[int, int]] = []
    _run(
        reader.get_spans(
            "trial-A",
            progress_callback=lambda fetched, total: progress.append((fetched, total)),
        )
    )

    # Cumulative fetched count after each page; total comes from the count query.
    assert progress == [(2, 5), (4, 5), (5, 5)]


def test_get_spans_skips_count_when_no_progress_callback() -> None:
    reader = _make_reader(page_size=10)
    mock_get = _scripted_get(
        [_FakeResponse(json_body=[_row("a")])]  # single short page
    )
    reader._client.get = mock_get  # type: ignore[method-assign]

    _run(reader.get_spans("trial-A"))

    # Without progress_callback, we issue one page fetch and no count query.
    assert mock_get.call_count == 1
    call = mock_get.call_args
    assert "select count" not in call.kwargs["params"]["query"]


def test_get_spans_count_query_runs_once_before_pagination() -> None:
    reader = _make_reader(page_size=10)
    mock_get = _scripted_get(
        [
            _FakeResponse(json_body=[{"cnt": "1"}]),
            _FakeResponse(json_body=[_row("a")]),
        ]
    )
    reader._client.get = mock_get  # type: ignore[method-assign]

    progress: list[tuple[int, int]] = []
    _run(
        reader.get_spans(
            "trial-A",
            progress_callback=lambda f, t: progress.append((f, t)),
        )
    )

    # 1 count query + 1 page.
    assert mock_get.call_count == 2
    first_call = mock_get.call_args_list[0]
    assert "select count" in first_call.kwargs["params"]["query"]
    assert progress == [(1, 1)]
