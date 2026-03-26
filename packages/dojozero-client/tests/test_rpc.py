"""Tests for RPC module."""

import tempfile
from pathlib import Path

import pytest

from dojozero_client._rpc import RPCClient, RPCError, RPCServer


class TestRPCServer:
    """Tests for RPCServer."""

    @pytest.mark.asyncio
    async def test_starts_and_stops(self):
        """Test server starts and stops cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"
            server = RPCServer(socket_path)

            await server.start()
            assert socket_path.exists()

            await server.stop()
            assert not socket_path.exists()

    @pytest.mark.asyncio
    async def test_removes_existing_socket_on_start(self):
        """Test server removes existing socket file on start."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"
            # Create stale socket file
            socket_path.touch()

            server = RPCServer(socket_path)
            await server.start()
            # Should succeed, not fail due to existing file
            assert socket_path.exists()
            await server.stop()

    @pytest.mark.asyncio
    async def test_registers_and_calls_handler(self):
        """Test handler registration and invocation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"
            server = RPCServer(socket_path)

            # Register a simple handler
            async def echo_handler(message: str) -> dict:
                return {"echo": message}

            server.register("echo", echo_handler)

            await server.start()
            try:
                # Call via client
                client = RPCClient(socket_path)
                result = await client.call("echo", message="hello")
                assert result == {"echo": "hello"}
            finally:
                await server.stop()

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_method(self):
        """Test unknown method returns error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"
            server = RPCServer(socket_path)

            await server.start()
            try:
                client = RPCClient(socket_path)
                with pytest.raises(RPCError) as exc_info:
                    await client.call("nonexistent")
                assert exc_info.value.code == "METHOD_NOT_FOUND"
            finally:
                await server.stop()

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(self):
        """Test handler exception is returned as RPC error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"
            server = RPCServer(socket_path)

            async def failing_handler() -> dict:
                raise ValueError("Something went wrong")

            server.register("fail", failing_handler)

            await server.start()
            try:
                client = RPCClient(socket_path)
                with pytest.raises(RPCError) as exc_info:
                    await client.call("fail")
                assert exc_info.value.code == "INTERNAL_ERROR"
                assert "Something went wrong" in exc_info.value.message
            finally:
                await server.stop()

    @pytest.mark.asyncio
    async def test_handler_rpc_error_preserved(self):
        """Test RPCError from handler preserves code and message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"
            server = RPCServer(socket_path)

            async def custom_error_handler() -> dict:
                raise RPCError("CUSTOM_ERROR", "Custom error message")

            server.register("custom_fail", custom_error_handler)

            await server.start()
            try:
                client = RPCClient(socket_path)
                with pytest.raises(RPCError) as exc_info:
                    await client.call("custom_fail")
                assert exc_info.value.code == "CUSTOM_ERROR"
                assert exc_info.value.message == "Custom error message"
            finally:
                await server.stop()


class TestRPCClient:
    """Tests for RPCClient."""

    @pytest.mark.asyncio
    async def test_raises_connection_error_when_no_server(self):
        """Test raises ConnectionError when server not running."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "nonexistent.sock"
            client = RPCClient(socket_path)

            with pytest.raises(ConnectionError):
                await client.call("any_method")

    @pytest.mark.asyncio
    async def test_async_call(self):
        """Test async call method works correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"
            server = RPCServer(socket_path)

            async def ping() -> dict:
                return {"status": "pong"}

            server.register("ping", ping)
            await server.start()

            try:
                client = RPCClient(socket_path)
                result = await client.call("ping")
                assert result == {"status": "pong"}
            finally:
                await server.stop()

    def test_is_daemon_running_returns_false_when_no_socket(self):
        """Test is_daemon_running returns False when no socket."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "nonexistent.sock"
            client = RPCClient(socket_path)
            assert client.is_daemon_running() is False

    @pytest.mark.asyncio
    async def test_can_connect_when_server_running(self):
        """Test client can connect when server accepts connections."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "test.sock"
            server = RPCServer(socket_path)

            async def ping() -> dict:
                return {"ok": True}

            server.register("ping", ping)
            await server.start()

            try:
                client = RPCClient(socket_path)
                # Verify we can make a call (proves connection works)
                result = await client.call("ping")
                assert result == {"ok": True}
            finally:
                await server.stop()


class TestRPCError:
    """Tests for RPCError exception."""

    def test_stores_code_and_message(self):
        """Test RPCError stores code and message."""
        error = RPCError("TEST_CODE", "Test message")
        assert error.code == "TEST_CODE"
        assert error.message == "Test message"

    def test_str_representation(self):
        """Test string representation includes code and message."""
        error = RPCError("ERR_CODE", "Error details")
        assert str(error) == "ERR_CODE: Error details"
