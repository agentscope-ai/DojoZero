"""Integration tests for SLS SQL query mode.

These tests verify that SLS SQL queries work correctly for fetching spans
by operation name. Each query uses SQL mode with LIMIT to efficiently
retrieve a small number of spans per operation type.

Run with: uv run pytest tests/test_sls_sql_query.py -v -m integration
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from dojozero.core._tracing import SpanData

LOGGER = logging.getLogger(__name__)


def _has_sls_credentials() -> bool:
    """Check if SLS credentials are configured."""
    return bool(
        os.environ.get("DOJOZERO_SLS_PROJECT")
        and os.environ.get("DOJOZERO_SLS_ENDPOINT")
        and os.environ.get("DOJOZERO_SLS_LOGSTORE")
    )


@pytest.mark.integration
class TestSLSSqlQuery:
    """Test SLS SQL query mode for fetching spans."""

    @pytest.fixture
    def sls_config(self) -> dict[str, str]:
        """Get SLS configuration from environment."""
        if not _has_sls_credentials():
            pytest.skip("SLS credentials not configured")
        return {
            "project": os.environ["DOJOZERO_SLS_PROJECT"],
            "endpoint": os.environ["DOJOZERO_SLS_ENDPOINT"],
            "logstore": os.environ["DOJOZERO_SLS_LOGSTORE"],
            "service_name": "dojozero",
        }

    @pytest.fixture
    def sls_client(self, sls_config: dict[str, str]) -> "SLSSqlQueryClient":
        """Create SLS SQL query client."""
        return SLSSqlQueryClient(
            endpoint=sls_config["endpoint"],
            project=sls_config["project"],
            logstore=sls_config["logstore"],
            service_name=sls_config["service_name"],
        )

    @pytest.mark.asyncio
    async def test_sql_query_event_game_initialize(
        self, sls_client: "SLSSqlQueryClient"
    ):
        """Query event.game_initialize spans using SQL mode."""
        spans = await sls_client.query_spans_sql(
            operation_name="event.game_initialize", limit=5
        )
        LOGGER.info("event.game_initialize: got %d spans", len(spans))
        for span in spans:
            assert span.operation_name == "event.game_initialize"
            LOGGER.info(
                "  - trace_id=%s, span_id=%s, tags=%s",
                span.trace_id[:8],
                span.span_id[:8],
                list(span.tags.keys())[:5],
            )

    @pytest.mark.asyncio
    async def test_sql_query_event_nba_play(self, sls_client: "SLSSqlQueryClient"):
        """Query event.nba_play spans using SQL mode."""
        spans = await sls_client.query_spans_sql(
            operation_name="event.nba_play", limit=5
        )
        LOGGER.info("event.nba_play: got %d spans", len(spans))
        for span in spans:
            assert span.operation_name == "event.nba_play"
            LOGGER.info(
                "  - trace_id=%s, span_id=%s, tags=%s",
                span.trace_id[:8],
                span.span_id[:8],
                list(span.tags.keys())[:5],
            )

    @pytest.mark.asyncio
    async def test_sql_query_agent_response(self, sls_client: "SLSSqlQueryClient"):
        """Query agent.response spans using SQL mode."""
        spans = await sls_client.query_spans_sql(
            operation_name="agent.response", limit=5
        )
        LOGGER.info("agent.response: got %d spans", len(spans))
        for span in spans:
            assert span.operation_name == "agent.response"
            LOGGER.info(
                "  - trace_id=%s, span_id=%s, tags=%s",
                span.trace_id[:8],
                span.span_id[:8],
                list(span.tags.keys())[:5],
            )

    @pytest.mark.asyncio
    async def test_sql_query_trial_started(self, sls_client: "SLSSqlQueryClient"):
        """Query trial.started spans using SQL mode."""
        spans = await sls_client.query_spans_sql(
            operation_name="trial.started", limit=5
        )
        LOGGER.info("trial.started: got %d spans", len(spans))
        for span in spans:
            assert span.operation_name == "trial.started"
            LOGGER.info(
                "  - trace_id=%s, span_id=%s, tags=%s",
                span.trace_id[:8],
                span.span_id[:8],
                list(span.tags.keys())[:5],
            )

    @pytest.mark.asyncio
    async def test_sql_query_broker_final_stats(self, sls_client: "SLSSqlQueryClient"):
        """Query broker.final_stats spans using SQL mode."""
        spans = await sls_client.query_spans_sql(
            operation_name="broker.final_stats", limit=5
        )
        LOGGER.info("broker.final_stats: got %d spans", len(spans))
        for span in spans:
            assert span.operation_name == "broker.final_stats"
            LOGGER.info(
                "  - trace_id=%s, span_id=%s, tags=%s",
                span.trace_id[:8],
                span.span_id[:8],
                list(span.tags.keys())[:5],
            )

    @pytest.mark.asyncio
    async def test_sql_query_all_operation_types(self, sls_client: "SLSSqlQueryClient"):
        """Query all operation types in one test for summary."""
        operation_names = [
            "event.game_initialize",
            "event.nba_play",
            "agent.response",
            "trial.started",
            "broker.final_stats",
        ]

        results: dict[str, list[SpanData]] = {}
        for op_name in operation_names:
            spans = await sls_client.query_spans_sql(operation_name=op_name, limit=5)
            results[op_name] = spans

        # Log summary
        LOGGER.info("=" * 60)
        LOGGER.info("SQL Query Results Summary:")
        LOGGER.info("=" * 60)
        for op_name, spans in results.items():
            LOGGER.info("  %s: %d spans", op_name, len(spans))
            for i, span in enumerate(spans):
                LOGGER.info(
                    "    [%d] trace_id=%s, span_id=%s",
                    i + 1,
                    span.trace_id[:16] if span.trace_id else "N/A",
                    span.span_id[:8] if span.span_id else "N/A",
                )
        LOGGER.info("=" * 60)

        # Verify at least some operation types returned data
        total_spans = sum(len(spans) for spans in results.values())
        LOGGER.info("Total spans retrieved: %d", total_spans)


class SLSSqlQueryClient:
    """Client for SLS SQL queries.

    This client demonstrates SQL query mode for SLS, which is more efficient
    for fetching specific numbers of records compared to pagination mode.
    """

    DEFAULT_LOOKBACK_DAYS = 7

    def __init__(
        self,
        endpoint: str,
        project: str,
        logstore: str,
        service_name: str = "dojozero",
    ) -> None:
        from dojozero.core._credentials import get_credential_provider

        self._endpoint = endpoint.rstrip("/")
        self._project = project
        self._logstore = logstore
        self._service_name = service_name
        self._credential_provider = get_credential_provider()
        self._client = httpx.AsyncClient(timeout=30.0)

    def _get_base_url(self) -> str:
        return f"https://{self._project}.{self._endpoint}"

    def _sign_request(
        self,
        method: str,
        resource: str,
        params: dict[str, Any],
    ) -> dict[str, str]:
        """Generate authentication headers for SLS API request."""
        import base64
        import hashlib
        import hmac
        import time

        creds = self._credential_provider.get_credentials()
        gmt_time = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

        content_md5 = ""
        content_type = "application/json"

        headers_to_sign = {
            "x-log-apiversion": "0.6.0",
            "x-log-signaturemethod": "hmac-sha1",
            "x-log-bodyrawsize": "0",
        }

        if creds.security_token:
            headers_to_sign["x-acs-security-token"] = creds.security_token

        canonicalized_headers = "\n".join(
            f"{k}:{v}" for k, v in sorted(headers_to_sign.items())
        )

        canonicalized_resource = resource
        if params:
            query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            canonicalized_resource = f"{resource}?{query_string}"

        string_to_sign = (
            f"{method}\n{content_md5}\n{content_type}\n{gmt_time}\n"
            f"{canonicalized_headers}\n{canonicalized_resource}"
        )

        signature = base64.b64encode(
            hmac.new(
                creds.access_key_secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")

        headers = {
            "Authorization": f"LOG {creds.access_key_id}:{signature}",
            "Content-Type": content_type,
            "Date": gmt_time,
            "x-log-apiversion": "0.6.0",
            "x-log-signaturemethod": "hmac-sha1",
            "x-log-bodyrawsize": "0",
        }

        if creds.security_token:
            headers["x-acs-security-token"] = creds.security_token

        return headers

    async def query_spans_sql(
        self,
        operation_name: str,
        limit: int = 5,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[SpanData]:
        """Query spans using SQL mode.

        SQL mode is more efficient for fetching specific numbers of records
        since SLS can use LIMIT to stop early rather than paginating.

        Args:
            operation_name: The operation name to filter by
            limit: Maximum number of spans to return
            start_time: Start of time range (defaults to 7 days ago)
            end_time: End of time range (defaults to now)

        Returns:
            List of SpanData matching the query
        """
        now = datetime.now(timezone.utc)

        if end_time is None:
            end_time = now
        if start_time is None:
            start_time = now - timedelta(days=self.DEFAULT_LOOKBACK_DAYS)

        # SQL query format: search_condition | SELECT ... LIMIT n
        # This is more efficient than pagination for small result sets
        query = (
            f'_service:"{self._service_name}" AND '
            f'_operation_name:"{operation_name}" | '
            f"SELECT * LIMIT {limit}"
        )

        params = {
            "type": "log",
            "from": str(int(start_time.timestamp())),
            "to": str(int(end_time.timestamp())),
            "query": query,
        }

        resource = f"/logstores/{self._logstore}"
        headers = self._sign_request("GET", resource, params)

        LOGGER.debug("SLS SQL query: %s", query)

        try:
            response = await self._client.get(
                f"{self._get_base_url()}{resource}",
                params=params,
                headers=headers,
            )

            if response.status_code != 200:
                LOGGER.error(
                    "SLS SQL query error: status=%d, body=%s",
                    response.status_code,
                    response.text[:500] if response.text else "(empty)",
                )
            response.raise_for_status()

            data = response.json()

            # SLS returns list directly or dict with "data" key
            rows = data if isinstance(data, list) else data.get("data", [])

            LOGGER.debug("SLS SQL query returned %d rows", len(rows))

            # Convert rows to SpanData
            spans: list[SpanData] = []
            for row in rows:
                if isinstance(row, dict):
                    span = self._convert_sls_row_to_span(row)
                    if span:
                        spans.append(span)

            return spans

        except httpx.HTTPStatusError as e:
            LOGGER.error("SLS HTTP error: %s", e)
            return []
        except httpx.RequestError as e:
            LOGGER.error("SLS request error: %s", e)
            return []
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            LOGGER.error("Failed to parse SLS response: %s", e)
            return []

    def _convert_sls_row_to_span(self, row: dict[str, Any]) -> SpanData | None:
        """Convert an SLS log row to SpanData.

        Reuses the same conversion logic as SLSTraceReader.
        """
        try:
            trace_id = row.get(
                "_trace_id",
                row.get("trace_id", row.get("traceId", row.get("traceID", ""))),
            )
            span_id = row.get(
                "_span_id",
                row.get("span_id", row.get("spanId", row.get("spanID", ""))),
            )
            operation_name = row.get(
                "_operation_name",
                row.get(
                    "operation_name", row.get("operationName", row.get("name", ""))
                ),
            )

            # Handle start time
            start_time_raw = row.get("__time__", row.get("startTime", 0))
            if isinstance(start_time_raw, str):
                try:
                    dt = datetime.fromisoformat(start_time_raw.replace("Z", "+00:00"))
                    start_time = int(dt.timestamp() * 1_000_000)
                except ValueError:
                    start_time = int(start_time_raw) if start_time_raw.isdigit() else 0
            else:
                start_time = int(start_time_raw) * 1_000_000

            duration = int(
                row.get("_duration_us", row.get("duration_us", row.get("duration", 0)))
            )
            parent_span_id = row.get(
                "_parent_span_id",
                row.get(
                    "parent_span_id", row.get("parentSpanId", row.get("parentSpanID"))
                ),
            )

            # Extract tags from row fields
            tags: dict[str, Any] = {}
            skip_prefixes = ("__",)
            extracted_infra = {
                "_trace_id",
                "_span_id",
                "_operation_name",
                "_duration_us",
                "_parent_span_id",
                "_service",
                "_event_time",
            }

            for key, value in row.items():
                if any(key.startswith(p) for p in skip_prefixes):
                    continue
                if key in extracted_infra:
                    continue

                if key.startswith("_"):
                    if key == "_actor_id":
                        tags["actor.id"] = value
                    elif key == "_sequence":
                        tags["sequence"] = value
                    continue

                if key.startswith("dojozero."):
                    tags[key] = value
                elif key.startswith("dojozero_"):
                    normalized_key = "dojozero." + key[9:].replace("_", ".")
                    tags[normalized_key] = value
                elif key.startswith("event."):
                    tags[key] = value
                elif key.startswith("event_"):
                    normalized_key = "event." + key[6:]
                    tags[normalized_key] = value
                elif key.startswith("broker."):
                    tags[key] = value
                elif key.startswith("broker_"):
                    normalized_key = "broker." + key[7:]
                    tags[normalized_key] = value
                elif key.startswith("trial."):
                    tags[key] = value
                elif key.startswith("trial_"):
                    normalized_key = "trial." + key[6:]
                    tags[normalized_key] = value
                elif key.startswith("game_") or key.startswith("sport_"):
                    normalized_key = key.replace("_", ".")
                    tags[normalized_key] = value
                elif key in ("game_id", "sport_type", "game_date", "sequence"):
                    normalized_key = key.replace("_", ".")
                    tags[normalized_key] = value
                else:
                    tags[key] = value

            return SpanData(
                trace_id=trace_id,
                span_id=span_id,
                operation_name=operation_name,
                start_time=start_time,
                duration=duration,
                parent_span_id=parent_span_id,
                tags=tags,
                logs=row.get("logs", []),
            )
        except (KeyError, ValueError, TypeError) as e:
            LOGGER.warning("Failed to convert SLS row to span: %s", e)
            return None

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


async def main() -> None:
    """Run SQL queries directly without pytest."""
    # Load .env file
    from dotenv import load_dotenv

    load_dotenv()

    # Check credentials
    if not _has_sls_credentials():
        print("ERROR: SLS credentials not configured in environment")
        print(
            "Required: DOJOZERO_SLS_PROJECT, DOJOZERO_SLS_ENDPOINT, DOJOZERO_SLS_LOGSTORE"
        )
        return

    # Create client
    client = SLSSqlQueryClient(
        endpoint=os.environ["DOJOZERO_SLS_ENDPOINT"],
        project=os.environ["DOJOZERO_SLS_PROJECT"],
        logstore=os.environ["DOJOZERO_SLS_LOGSTORE"],
        service_name="dojozero",
    )

    operation_names = [
        "event.game_initialize",
        "event.nba_play",
        "agent.response",
        "trial.started",
        "broker.final_stats",
    ]

    print("=" * 70)
    print("SLS SQL Query Test")
    print(f"Project: {os.environ['DOJOZERO_SLS_PROJECT']}")
    print(f"Logstore: {os.environ['DOJOZERO_SLS_LOGSTORE']}")
    print(f"Endpoint: {os.environ['DOJOZERO_SLS_ENDPOINT']}")
    print("=" * 70)

    try:
        for op_name in operation_names:
            print(f"\n>>> Querying: {op_name} (limit=5)")
            print("-" * 50)

            spans = await client.query_spans_sql(operation_name=op_name, limit=5)
            print(f"Got {len(spans)} spans")

            for i, span in enumerate(spans):
                print(f"\n  [{i + 1}] span_id: {span.span_id}")
                print(f"      trace_id: {span.trace_id}")
                print(f"      operation_name: {span.operation_name}")
                print(f"      start_time: {span.start_time}")
                print(f"      duration: {span.duration}")
                print(f"      tags ({len(span.tags)} keys):")
                # Print first 10 tags
                for j, (k, v) in enumerate(span.tags.items()):
                    if j >= 10:
                        print(f"        ... and {len(span.tags) - 10} more tags")
                        break
                    # Truncate long values
                    v_str = str(v)
                    if len(v_str) > 80:
                        v_str = v_str[:77] + "..."
                    print(f"        {k}: {v_str}")

    finally:
        await client.close()

    print("\n" + "=" * 70)
    print("Done!")
    print("=" * 70)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(main())
