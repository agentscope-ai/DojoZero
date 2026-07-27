"""Tests for DojoClient."""

from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from dojozero_client._client import (
    ChatMessage,
    DojoClient,
    GatewayInfo,
    TrialConnection,
)
from dojozero_client._exceptions import ConnectionError as DojoConnectionError


class TestChatMessage:
    """Tests for ChatMessage dataclass."""

    def test_from_dict(self):
        """Test creating ChatMessage from a gateway API response."""
        data = {
            "messageId": "msg123",
            "trialId": "trial123",
            "agentId": "agent1",
            "content": "away_win still looks good",
            "createdAt": "2024-01-01T00:00:00Z",
        }
        message = ChatMessage.from_dict(data)
        assert message.message_id == "msg123"
        assert message.trial_id == "trial123"
        assert message.agent_id == "agent1"
        assert message.content == "away_win still looks good"
        assert message.created_at == datetime(2024, 1, 1, tzinfo=timezone.utc)


class TestTrialConnectionChat:
    """Tests for TrialConnection chat methods."""

    def _make_connection(self):
        transport = MagicMock()
        transport.request = AsyncMock()
        return TrialConnection(
            transport=transport, agent_id="agent1", trial_id="trial123"
        ), transport

    @pytest.mark.asyncio
    async def test_send_message_posts_content(self):
        """send_message POSTs to /messages and parses the response."""
        connection, transport = self._make_connection()
        transport.request.return_value = {
            "messageId": "msg123",
            "trialId": "trial123",
            "agentId": "agent1",
            "content": "gg",
            "createdAt": "2024-01-01T00:00:00Z",
        }

        result = await connection.send_message("gg")

        assert result.message_id == "msg123"
        transport.request.assert_called_once_with(
            "POST", "/messages", json={"content": "gg"}
        )

    @pytest.mark.asyncio
    async def test_send_message_includes_idempotency_key(self):
        """send_message forwards an idempotency key when provided."""
        connection, transport = self._make_connection()
        transport.request.return_value = {
            "messageId": "msg123",
            "trialId": "trial123",
            "agentId": "agent1",
            "content": "gg",
            "createdAt": "2024-01-01T00:00:00Z",
        }

        await connection.send_message("gg", idempotency_key="key-1")

        transport.request.assert_called_once_with(
            "POST",
            "/messages",
            json={"content": "gg", "idempotencyKey": "key-1"},
        )

    @pytest.mark.asyncio
    async def test_get_messages_parses_list(self):
        """get_messages GETs /messages and parses each message."""
        connection, transport = self._make_connection()
        transport.request.return_value = {
            "messages": [
                {
                    "messageId": "msg1",
                    "trialId": "trial123",
                    "agentId": "agent1",
                    "content": "first",
                    "createdAt": "2024-01-01T00:00:00Z",
                },
                {
                    "messageId": "msg2",
                    "trialId": "trial123",
                    "agentId": "agent2",
                    "content": "second",
                    "createdAt": "2024-01-01T00:01:00Z",
                },
            ]
        }

        result = await connection.get_messages(limit=10)

        assert len(result) == 2
        assert result[0].content == "first"
        assert result[1].agent_id == "agent2"
        transport.request.assert_called_once_with(
            "GET", "/messages", params={"limit": 10}
        )


class TestGatewayInfo:
    """Tests for GatewayInfo dataclass."""

    def test_from_dict(self):
        """Test creating GatewayInfo from API response."""
        data = {
            "trial_id": "trial-123",
            "endpoint": "/api/trials/trial-123",
        }
        info = GatewayInfo.from_dict(data)
        assert info.trial_id == "trial-123"
        assert info.endpoint == "/api/trials/trial-123"
        assert info.url is None

    def test_from_dict_with_url(self):
        """Test creating GatewayInfo with URL."""
        data = {
            "trial_id": "trial-123",
            "endpoint": "/api/trials/trial-123",
            "url": "http://localhost:8000/api/trials/trial-123",
        }
        info = GatewayInfo.from_dict(data)
        assert info.url == "http://localhost:8000/api/trials/trial-123"

    def test_url_field_optional(self):
        """Test URL field is optional."""
        info = GatewayInfo(trial_id="abc", endpoint="/api/trials/abc")
        assert info.url is None

        info_with_url = GatewayInfo(
            trial_id="abc",
            endpoint="/api/trials/abc",
            url="http://localhost:8000/api/trials/abc",
        )
        assert info_with_url.url == "http://localhost:8000/api/trials/abc"


class TestDojoClient:
    """Tests for DojoClient."""

    def test_init_default(self):
        """Test client initialization with defaults."""
        client = DojoClient()
        assert client._timeout == 30.0

    def test_init_with_dashboard_url(self):
        """Test client initialization with dashboard URL."""
        client = DojoClient(dashboard_url="http://localhost:8080")
        assert client._config.dashboard_url == "http://localhost:8080"

    def test_init_with_dashboard_urls(self):
        """Test client initialization with dashboard URLs."""
        client = DojoClient(dashboard_urls=["http://dash-a:8000", "http://dash-b:8000"])
        assert client._config.dashboard_urls == [
            "http://dash-a:8000",
            "http://dash-b:8000",
        ]

    def test_init_with_timeout(self):
        """Test client initialization with custom timeout."""
        client = DojoClient(timeout=60.0)
        assert client._timeout == 60.0


class TestDojoClientListGateways:
    """Tests for DojoClient.list_gateways."""

    @pytest.mark.asyncio
    async def test_list_gateways_success(self):
        """Test listing gateways from dashboard."""
        client = DojoClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "gateways": [
                {"trial_id": "trial-1", "endpoint": "/api/trials/trial-1"},
                {"trial_id": "trial-2", "endpoint": "/api/trials/trial-2"},
            ],
            "count": 2,
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            gateways = await client.list_gateways("http://localhost:8000")

            assert len(gateways) == 2
            assert gateways[0].trial_id == "trial-1"
            assert gateways[1].trial_id == "trial-2"

    @pytest.mark.asyncio
    async def test_list_gateways_empty(self):
        """Test listing gateways when none available."""
        client = DojoClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"gateways": [], "count": 0}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            gateways = await client.list_gateways("http://localhost:8000")

            assert len(gateways) == 0


class TestDojoClientDiscoverTrials:
    """Tests for DojoClient.discover_trials."""

    @pytest.mark.asyncio
    async def test_discover_trials_single_dashboard(self):
        """Test discovering trials from single dashboard."""
        client = DojoClient(dashboard_url="http://localhost:8000")

        with patch.object(client, "list_gateways") as mock_list:
            mock_list.return_value = [
                GatewayInfo(trial_id="trial-1", endpoint="/api/trials/trial-1"),
            ]

            gateways = await client.discover_trials()

            assert len(gateways) == 1
            assert gateways[0].trial_id == "trial-1"
            assert gateways[0].url == "http://localhost:8000/api/trials/trial-1"

    @pytest.mark.asyncio
    async def test_discover_trials_multiple_dashboards(self):
        """Test discovering trials from multiple dashboards."""
        client = DojoClient(dashboard_urls=["http://dash-a:8000", "http://dash-b:8000"])

        async def mock_list_gateways(url):
            if "dash-a" in url:
                return [GatewayInfo(trial_id="trial-1", endpoint="/api/trials/trial-1")]
            else:
                return [GatewayInfo(trial_id="trial-2", endpoint="/api/trials/trial-2")]

        with patch.object(client, "list_gateways", side_effect=mock_list_gateways):
            gateways = await client.discover_trials()

            assert len(gateways) == 2
            trial_ids = {g.trial_id for g in gateways}
            assert trial_ids == {"trial-1", "trial-2"}

            # Check URLs are correct
            for gw in gateways:
                if gw.trial_id == "trial-1":
                    assert gw.url == "http://dash-a:8000/api/trials/trial-1"
                else:
                    assert gw.url == "http://dash-b:8000/api/trials/trial-2"

    @pytest.mark.asyncio
    async def test_discover_trials_partial_failure(self):
        """Test discovering trials when some dashboards fail."""
        client = DojoClient(dashboard_urls=["http://dash-a:8000", "http://dash-b:8000"])

        async def mock_list_gateways(url):
            if "dash-a" in url:
                return [GatewayInfo(trial_id="trial-1", endpoint="/api/trials/trial-1")]
            else:
                raise ConnectionError("Dashboard unavailable")

        with patch.object(client, "list_gateways", side_effect=mock_list_gateways):
            gateways = await client.discover_trials()

            # Should still return results from working dashboard
            assert len(gateways) == 1
            assert gateways[0].trial_id == "trial-1"

    @pytest.mark.asyncio
    async def test_discover_trials_all_fail(self):
        """Test discovering trials when all dashboards fail."""
        client = DojoClient(dashboard_urls=["http://dash-a:8000", "http://dash-b:8000"])

        async def mock_list_gateways(url):
            raise ConnectionError("Dashboard unavailable")

        with patch.object(client, "list_gateways", side_effect=mock_list_gateways):
            with pytest.raises(
                DojoConnectionError, match="All .* dashboards unreachable"
            ):
                await client.discover_trials()


class TestReconnection:
    """Tests for reconnection when agent already registered."""

    def test_extract_agent_id_from_error_message(self):
        """Test extracting agent_id from error message."""
        import re

        # Test format: "Agent qwenpaw-agent already connected"
        error_msg = "Agent qwenpaw-agent already connected"
        match = re.search(r"Agent (\S+) already", error_msg)
        assert match is not None
        assert match.group(1) == "qwenpaw-agent"

    def test_extract_agent_id_from_json_error(self):
        """Test extracting agent_id from JSON error."""
        import re

        # Test format from API response
        error_msg = '{"detail":{"error":{"code":"ALREADY_REGISTERED","message":"Agent test-bot already connected","details":{}}}}'
        match = re.search(r'"message":\s*"Agent (\S+) already', error_msg)
        assert match is not None
        assert match.group(1) == "test-bot"

    def test_extract_agent_id_with_hyphen(self):
        """Test extracting agent_id with hyphens."""
        import re

        error_msg = "Agent my-long-agent-name already connected"
        match = re.search(r"Agent (\S+) already", error_msg)
        assert match is not None
        assert match.group(1) == "my-long-agent-name"
