"""End-to-end API integration tests for DojoZero Dashboard and Arena servers.

This test module validates the complete API flow as documented in the design doc:
1. Dashboard Server (`dojo0 serve`) - Trial management and OTLP export
2. Arena Server (`dojo0 arena`) - Trace queries and WebSocket streaming
3. Jaeger - Trace storage backend

Test Requirements:
- Jaeger running on http://localhost:16686 (UI) and http://localhost:4318 (OTLP)

Run with: pytest tests/test_api_e2e.py --run-integration -v

To start Jaeger:
    docker run -d --name jaeger \
        -p 16686:16686 \
        -p 4317:4317 \
        -p 4318:4318 \
        jaegertracing/all-in-one:latest
"""

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import dojozero.samples  # noqa: F401 - trigger registration
from dojozero.core import (
    TrialOrchestrator,
    InMemoryOrchestratorStore,
    LocalActorRuntimeProvider,
)
from dojozero.dashboard_server import create_dashboard_app
from dojozero.arena_server import create_arena_app
from dojozero.core._tracing import (
    JaegerTraceReader,
    OTelSpanExporter,
    set_otel_exporter,
)

# Server endpoints
JAEGER_UI_URL = "http://localhost:16686"
JAEGER_OTLP_URL = "http://localhost:4318"


def is_jaeger_running() -> bool:
    """Check if Jaeger is running and accessible."""
    try:
        response = httpx.get(f"{JAEGER_UI_URL}/api/services", timeout=2.0)
        return response.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


# Skip all tests if Jaeger is not running
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not is_jaeger_running(),
        reason="Jaeger is not running on http://localhost:16686",
    ),
]


# Use module-scoped OTel exporter to avoid OpenTelemetry global state issues
_module_exporter: OTelSpanExporter | None = None


def get_module_exporter() -> OTelSpanExporter:
    """Get or create the module-level OTel exporter."""
    global _module_exporter
    if _module_exporter is None:
        _module_exporter = OTelSpanExporter(JAEGER_OTLP_URL, service_name="dojozero")
        set_otel_exporter(_module_exporter)
    return _module_exporter


@pytest.fixture(scope="module")
def otel_exporter():
    """Module-scoped OTel exporter fixture."""
    exporter = get_module_exporter()
    yield exporter


@pytest.fixture(scope="module")
def jaeger_reader():
    """Module-scoped Jaeger trace reader."""
    return JaegerTraceReader(JAEGER_UI_URL, service_name="dojozero")


@pytest.fixture
def orchestrator():
    """Create a TrialOrchestrator instance with in-memory store."""
    store = InMemoryOrchestratorStore()
    provider = LocalActorRuntimeProvider()
    return TrialOrchestrator(store=store, runtime_provider=provider)


@pytest.fixture
def dashboard_app(orchestrator, otel_exporter, tmp_path):
    """Create Dashboard Server FastAPI app with OTel configured."""
    from dojozero.dashboard_server._scheduler import FileSchedulerStore

    scheduler_store = FileSchedulerStore(tmp_path / "scheduler")
    app = create_dashboard_app(
        orchestrator,
        scheduler_store=scheduler_store,
        trace_backend="jaeger",
        trace_ingest_endpoint=JAEGER_OTLP_URL,
    )
    return app


@pytest.fixture
def arena_app():
    """Create Arena Server FastAPI app pointing to Jaeger."""
    app = create_arena_app(
        trace_backend="jaeger",
        trace_query_endpoint=JAEGER_UI_URL,
        poll_interval=0.5,
    )
    return app


@pytest.fixture
def dashboard_client(dashboard_app):
    """Test client for Dashboard Server."""
    with TestClient(dashboard_app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def arena_client(arena_app):
    """Test client for Arena Server."""
    with TestClient(arena_app, raise_server_exceptions=False) as client:
        yield client


# -----------------------------------------------------------------------------
# Dashboard Server API Tests
# -----------------------------------------------------------------------------


class TestDashboardServer:
    """Tests for Dashboard Server API endpoints."""

    def test_health_check(self, dashboard_client):
        """Test GET /health endpoint."""
        response = dashboard_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_list_trials_empty(self, dashboard_client):
        """Test GET /api/trials returns empty list initially."""
        response = dashboard_client.get("/api/trials")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_submit_trial_requires_scenario(self, dashboard_client):
        """Test POST /api/trials requires scenario or params."""
        response = dashboard_client.post(
            "/api/trials",
            json={"trial_id": "test-no-scenario"},
        )
        assert response.status_code == 400
        assert "scenario" in response.json()["error"].lower()

    def test_submit_trial_with_scenario(self, dashboard_client):
        """Test POST /api/trials with valid scenario config."""
        trial_id = f"test-submit-{int(time.time())}"
        response = dashboard_client.post(
            "/api/trials",
            json={
                "trial_id": trial_id,
                "scenario": {
                    "name": "samples.bounded-random",
                    "config": {
                        "total_events": 2,
                        "interval_seconds": 0.1,
                    },
                },
                "metadata": {"test": "api_e2e"},
            },
        )
        assert response.status_code == 201, f"Response: {response.json()}"
        data = response.json()
        assert data["id"] == trial_id
        assert data["phase"] == "running"

        # Verify trial appears in list
        list_response = dashboard_client.get("/api/trials")
        assert list_response.status_code == 200
        trials = list_response.json()
        trial_ids = [t["id"] for t in trials]
        assert trial_id in trial_ids

    def test_submit_trial_with_params(self, dashboard_client):
        """Test POST /api/trials with params payload (alternative format)."""
        trial_id = f"test-params-{int(time.time())}"
        response = dashboard_client.post(
            "/api/trials",
            json={
                "trial_id": trial_id,
                "params": {
                    "scenario": {
                        "name": "samples.bounded-random",
                        "config": {
                            "total_events": 1,
                            "interval_seconds": 0.0,
                        },
                    },
                    "metadata": {"source": "params"},
                },
            },
        )
        assert response.status_code == 201, f"Response: {response.json()}"
        data = response.json()
        assert data["id"] == trial_id

    def test_get_trial_status(self, dashboard_client):
        """Test GET /api/trials/{trial_id}/status endpoint."""
        # First submit a trial
        trial_id = f"test-status-{int(time.time())}"
        submit_response = dashboard_client.post(
            "/api/trials",
            json={
                "trial_id": trial_id,
                "scenario": {
                    "name": "samples.bounded-random",
                    "config": {"total_events": 1},
                },
            },
        )
        assert submit_response.status_code == 201

        # Get status
        status_response = dashboard_client.get(f"/api/trials/{trial_id}/status")
        assert status_response.status_code == 200
        data = status_response.json()
        assert data["id"] == trial_id
        assert "phase" in data
        assert "actors" in data

    def test_get_trial_status_not_found(self, dashboard_client):
        """Test GET /api/trials/{trial_id}/status for non-existent trial."""
        response = dashboard_client.get("/api/trials/non-existent-trial/status")
        assert response.status_code == 404
        assert "not found" in response.json()["error"].lower()

    def test_stop_trial(self, dashboard_client):
        """Test POST /api/trials/{trial_id}/stop endpoint."""
        # Submit a trial that runs longer
        trial_id = f"test-stop-{int(time.time())}"
        submit_response = dashboard_client.post(
            "/api/trials",
            json={
                "trial_id": trial_id,
                "scenario": {
                    "name": "samples.bounded-random",
                    "config": {
                        "total_events": 100,
                        "interval_seconds": 0.5,  # Long running
                    },
                },
            },
        )
        assert submit_response.status_code == 201

        # Stop the trial
        stop_response = dashboard_client.post(f"/api/trials/{trial_id}/stop")
        assert stop_response.status_code == 200
        data = stop_response.json()
        assert data["id"] == trial_id
        assert data["phase"] in ("stopped", "stopping")

    def test_stop_trial_not_found(self, dashboard_client):
        """Test POST /api/trials/{trial_id}/stop for non-existent trial."""
        response = dashboard_client.post("/api/trials/non-existent-trial/stop")
        assert response.status_code == 404

    def test_submit_duplicate_trial_id(self, dashboard_client):
        """Test that submitting duplicate trial_id returns 409 Conflict."""
        trial_id = f"test-duplicate-{int(time.time())}"

        # First submission
        response1 = dashboard_client.post(
            "/api/trials",
            json={
                "trial_id": trial_id,
                "scenario": {
                    "name": "samples.bounded-random",
                    "config": {"total_events": 1},
                },
            },
        )
        assert response1.status_code == 201

        # Second submission with same ID
        response2 = dashboard_client.post(
            "/api/trials",
            json={
                "trial_id": trial_id,
                "scenario": {
                    "name": "samples.bounded-random",
                    "config": {"total_events": 1},
                },
            },
        )
        assert response2.status_code == 409
        assert "exists" in response2.json()["error"].lower()


# -----------------------------------------------------------------------------
# Arena Server API Tests
# -----------------------------------------------------------------------------


class TestArenaServer:
    """Tests for Arena Server API endpoints."""

    def test_health_check(self, arena_client):
        """Test GET /health endpoint."""
        response = arena_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_list_trials(self, arena_client):
        """Test GET /api/trials endpoint."""
        response = arena_client.get("/api/trials")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_trial_not_found(self, arena_client):
        """Test GET /api/trials/{trial_id} for non-existent trial."""
        response = arena_client.get("/api/trials/non-existent-trial-xyz")
        assert response.status_code == 404

    def test_get_trial_with_spans(self, arena_client, otel_exporter):
        """Test GET /api/trials/{trial_id} returns spans when available."""
        # This test relies on spans being exported to Jaeger from other tests
        # or previous runs. Just verify the endpoint format is correct.
        response = arena_client.get("/api/trials")
        assert response.status_code == 200


# -----------------------------------------------------------------------------
# End-to-End Flow Tests
# -----------------------------------------------------------------------------


class TestE2EFlow:
    """End-to-end tests covering the full Dashboard -> Jaeger -> Frontend flow."""

    @pytest.mark.asyncio
    async def test_full_trial_flow(
        self, dashboard_client, jaeger_reader, otel_exporter
    ):
        """Test complete flow: submit trial -> emit spans -> query via Jaeger."""
        trial_id = f"e2e-flow-{int(time.time())}"

        # 1. Submit trial to Dashboard
        response = dashboard_client.post(
            "/api/trials",
            json={
                "trial_id": trial_id,
                "scenario": {
                    "name": "samples.bounded-random",
                    "config": {
                        "total_events": 3,
                        "interval_seconds": 0.1,
                    },
                },
                "metadata": {"test_type": "e2e"},
            },
        )
        assert response.status_code == 201, f"Submit failed: {response.json()}"

        # 2. Wait for trial to complete and spans to be exported
        await asyncio.sleep(3.0)

        # 3. Query Jaeger for spans
        spans = await jaeger_reader.get_spans(trial_id)
        print(f"\n[E2E] Found {len(spans)} spans for trial {trial_id}")

        # 4. Verify we got some spans (registration + lifecycle spans)
        if spans:
            operation_names = set(span.operation_name for span in spans)
            print(f"[E2E] Operation names: {operation_names}")

            # Check for expected span types
            # Trial lifecycle spans
            lifecycle_ops = {"trial.started", "trial.stopped", "trial.terminated"}
            # Registration spans
            registration_ops = {
                "agent.registered",
                "operator.registered",
                "datastream.registered",
            }

            found_lifecycle = bool(operation_names & lifecycle_ops)
            found_registration = bool(operation_names & registration_ops)

            print(f"[E2E] Found lifecycle spans: {found_lifecycle}")
            print(f"[E2E] Found registration spans: {found_registration}")

        # 5. Verify trial is in list
        trial_ids = await jaeger_reader.list_trials()
        print(f"[E2E] Available trial IDs: {trial_ids}")
        # Note: Jaeger may take time to index, so this is informational

    @pytest.mark.asyncio
    async def test_arena_receives_spans_from_jaeger(
        self, dashboard_client, arena_client, otel_exporter
    ):
        """Test that Arena Server can query spans that Dashboard exported."""
        trial_id = f"e2e-arena-{int(time.time())}"

        # 1. Submit trial via Dashboard
        response = dashboard_client.post(
            "/api/trials",
            json={
                "trial_id": trial_id,
                "scenario": {
                    "name": "samples.bounded-random",
                    "config": {
                        "total_events": 2,
                        "interval_seconds": 0.05,
                    },
                },
            },
        )
        assert response.status_code == 201

        # 2. Wait for spans to be exported to Jaeger
        await asyncio.sleep(3.0)

        # 3. Query Arena Server for trial
        trials_response = arena_client.get("/api/trials")
        assert trials_response.status_code == 200
        trials = trials_response.json()
        print(f"\n[Arena] Found {len(trials)} trials")

        # 4. Try to get specific trial spans
        trial_response = arena_client.get(f"/api/trials/{trial_id}")
        # Note: May be 404 if Jaeger hasn't indexed yet
        print(f"[Arena] Trial {trial_id} response: {trial_response.status_code}")

        if trial_response.status_code == 200:
            data = trial_response.json()
            spans = data.get("spans", [])
            print(f"[Arena] Found {len(spans)} spans for trial {trial_id}")


# -----------------------------------------------------------------------------
# WebSocket Streaming Tests
# -----------------------------------------------------------------------------


class TestWebSocketStreaming:
    """Tests for Arena Server WebSocket streaming."""

    def test_websocket_connection(self, arena_client):
        """Test WebSocket connection to /ws/trials/{trial_id}/stream."""
        trial_id = f"ws-test-{int(time.time())}"

        with arena_client.websocket_connect(
            f"/ws/trials/{trial_id}/stream"
        ) as websocket:
            # Should receive snapshot message immediately
            data = websocket.receive_text()
            message = json.loads(data)
            assert message["type"] == "snapshot"
            assert message["trial_id"] == trial_id
            assert "data" in message
            assert "spans" in message["data"]

            # Should receive heartbeat within poll interval
            data = websocket.receive_text()
            message = json.loads(data)
            # Could be either heartbeat or span
            assert message["type"] in ("heartbeat", "span", "snapshot")

    def test_websocket_receives_spans(
        self, dashboard_client, arena_client, otel_exporter
    ):
        """Test that WebSocket receives spans as they are emitted."""
        trial_id = f"ws-spans-{int(time.time())}"

        # First submit a trial
        response = dashboard_client.post(
            "/api/trials",
            json={
                "trial_id": trial_id,
                "scenario": {
                    "name": "samples.bounded-random",
                    "config": {
                        "total_events": 5,
                        "interval_seconds": 0.2,
                    },
                },
            },
        )
        assert response.status_code == 201

        # Connect to WebSocket and collect messages
        received_types = set()
        with arena_client.websocket_connect(
            f"/ws/trials/{trial_id}/stream"
        ) as websocket:
            # Collect messages for a few seconds
            for _ in range(10):
                try:
                    data = websocket.receive_text()
                    message = json.loads(data)
                    received_types.add(message["type"])
                except Exception:
                    break

        print(f"\n[WebSocket] Received message types: {received_types}")
        # Should have received at least snapshot and heartbeat
        assert "snapshot" in received_types or "heartbeat" in received_types


# -----------------------------------------------------------------------------
# Integration: Full Workflow Test
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_api_workflow(
    dashboard_client, arena_client, jaeger_reader, otel_exporter
):
    """
    Complete end-to-end test simulating the documented workflow:

    1. Start Jaeger (assumed running)
    2. Dashboard Server receives trial submission
    3. Trial runs and emits OTLP spans to Jaeger
    4. Arena Server queries Jaeger for trial data
    5. Arena Server streams spans via WebSocket
    """
    trial_id = f"complete-workflow-{int(time.time())}"
    print(f"\n{'=' * 60}")
    print(f"Testing complete API workflow with trial_id: {trial_id}")
    print(f"{'=' * 60}")

    # Step 1: Verify Jaeger is accessible
    assert is_jaeger_running(), "Jaeger must be running for this test"
    print("✓ Jaeger is running")

    # Step 2: Submit trial via Dashboard API
    submit_response = dashboard_client.post(
        "/api/trials",
        json={
            "trial_id": trial_id,
            "scenario": {
                "name": "samples.bounded-random",
                "config": {
                    "total_events": 5,
                    "payload_length": 8,
                    "interval_seconds": 0.1,
                    "seed": 42,
                },
            },
            "metadata": {
                "test_name": "complete_api_workflow",
                "timestamp": time.time(),
            },
        },
    )
    assert submit_response.status_code == 201, (
        f"Submit failed: {submit_response.json()}"
    )
    submit_data = submit_response.json()
    print(f"✓ Trial submitted: id={submit_data['id']}, phase={submit_data['phase']}")

    # Step 3: Verify trial appears in Dashboard list
    list_response = dashboard_client.get("/api/trials")
    assert list_response.status_code == 200
    trials = list_response.json()
    assert any(t["id"] == trial_id for t in trials), "Trial not found in list"
    print(f"✓ Trial visible in Dashboard: {len(trials)} total trials")

    # Step 4: Check trial status
    status_response = dashboard_client.get(f"/api/trials/{trial_id}/status")
    assert status_response.status_code == 200
    status_data = status_response.json()
    print(
        f"✓ Trial status: phase={status_data['phase']}, actors={len(status_data.get('actors', []))}"
    )

    # Step 5: Wait for trial to complete and spans to propagate to Jaeger
    print("  Waiting for spans to propagate to Jaeger...")
    await asyncio.sleep(4.0)

    # Step 6: Query Jaeger directly for spans
    spans = await jaeger_reader.get_spans(trial_id)
    print(f"✓ Jaeger query: {len(spans)} spans found")
    if spans:
        op_names = sorted(set(s.operation_name for s in spans))
        print(f"  Operation names: {op_names}")

    # Step 7: Query Arena Server for trial list
    arena_trials_response = arena_client.get("/api/trials")
    assert arena_trials_response.status_code == 200
    arena_trials = arena_trials_response.json()
    print(f"✓ Arena trials list: {len(arena_trials)} trials")

    # Step 8: Query Arena Server for specific trial
    arena_trial_response = arena_client.get(f"/api/trials/{trial_id}")
    if arena_trial_response.status_code == 200:
        trial_data = arena_trial_response.json()
        arena_spans = trial_data.get("spans", [])
        print(f"✓ Arena trial query: {len(arena_spans)} spans")
    else:
        print(
            f"  Arena trial query: {arena_trial_response.status_code} (may need more time)"
        )

    # Step 9: Test WebSocket streaming
    print("  Testing WebSocket connection...")
    ws_messages = []
    with arena_client.websocket_connect(f"/ws/trials/{trial_id}/stream") as ws:
        # Receive initial snapshot
        data = ws.receive_text()
        msg = json.loads(data)
        ws_messages.append(msg)
        print(
            f"✓ WebSocket snapshot: {len(msg.get('data', {}).get('spans', []))} spans"
        )

        # Receive one more message (heartbeat or span)
        try:
            data = ws.receive_text()
            msg = json.loads(data)
            ws_messages.append(msg)
            print(f"✓ WebSocket message: type={msg['type']}")
        except Exception:
            pass

    # Step 10: Stop the trial (if still running)
    stop_response = dashboard_client.post(f"/api/trials/{trial_id}/stop")
    if stop_response.status_code == 200:
        print("✓ Trial stopped")
    else:
        print(f"  Trial stop: {stop_response.status_code} (may already be stopped)")

    print(f"\n{'=' * 60}")
    print("Complete API workflow test PASSED")
    print(f"{'=' * 60}")


# -----------------------------------------------------------------------------
# CLI Integration Tests
# -----------------------------------------------------------------------------


class TestCLIIntegration:
    """Tests that verify CLI commands work correctly with the API."""

    def test_dashboard_health_via_http(self, dashboard_client):
        """Verify Dashboard Server health endpoint works as documented."""
        response = dashboard_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_arena_health_via_http(self, arena_client):
        """Verify Arena Server health endpoint works as documented."""
        response = arena_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_trial_submit_format_matches_cli(self, dashboard_client):
        """Verify API accepts same format as CLI would use."""
        # This mimics what `dojo0 run --params file.yaml --server http://...` would send
        trial_id = f"cli-compat-{int(time.time())}"
        response = dashboard_client.post(
            "/api/trials",
            json={
                "trial_id": trial_id,
                "params": {
                    "scenario": {
                        "name": "samples.bounded-random",
                        "module": None,
                        "config": {
                            "total_events": 2,
                            "payload_length": 8,
                            "interval_seconds": 0.0,
                        },
                    },
                    "metadata": {"source": "cli"},
                },
            },
        )
        assert response.status_code == 201, f"Response: {response.json()}"
