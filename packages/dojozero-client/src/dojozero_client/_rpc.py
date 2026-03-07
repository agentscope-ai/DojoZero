"""Unix socket RPC for daemon communication.

Provides a JSON-RPC server and client for secure local IPC.
The daemon runs an RPCServer, and CLI commands use RPCClient to communicate.

Protocol:
    Request:  {"id": "uuid", "method": "bet", "params": {"trial_id": "abc", ...}}
    Response: {"id": "uuid", "result": {...}}
    Error:    {"id": "uuid", "error": {"code": "...", "message": "..."}}
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Maximum message size (64KB should be plenty for RPC)
MAX_MESSAGE_SIZE = 65536


class RPCError(Exception):
    """RPC error with code and message."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class RPCServer:
    """Unix socket JSON-RPC server.

    Usage:
        server = RPCServer(Path("/tmp/daemon.sock"))
        server.register("echo", lambda msg: {"echo": msg})
        await server.start()
        # ... server runs until stopped
        await server.stop()
    """

    def __init__(self, socket_path: Path):
        """Initialize RPC server.

        Args:
            socket_path: Path to Unix socket file
        """
        self.socket_path = socket_path
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._server: asyncio.Server | None = None

    def register(self, method: str, handler: Callable[..., Awaitable[Any]]) -> None:
        """Register an RPC method handler.

        Args:
            method: Method name
            handler: Async function to handle the method
        """
        self._handlers[method] = handler

    async def start(self) -> None:
        """Start the RPC server."""
        # Remove existing socket file
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Ensure parent directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )

        # Set socket permissions (owner only)
        import os
        import stat

        os.chmod(self.socket_path, stat.S_IRUSR | stat.S_IWUSR)
        logger.info("RPC server listening on %s", self.socket_path)

    async def stop(self) -> None:
        """Stop the RPC server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self.socket_path.exists():
            self.socket_path.unlink()
            logger.info("RPC server stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a client connection."""
        try:
            # Read request
            data = await reader.read(MAX_MESSAGE_SIZE)
            if not data:
                return

            try:
                request = json.loads(data.decode())
            except json.JSONDecodeError as e:
                response = {
                    "id": None,
                    "error": {"code": "PARSE_ERROR", "message": str(e)},
                }
                writer.write(json.dumps(response).encode())
                await writer.drain()
                return

            method = request.get("method")
            params = request.get("params", {})
            req_id = request.get("id", str(uuid.uuid4()))

            # Dispatch to handler
            if method not in self._handlers:
                response = {
                    "id": req_id,
                    "error": {
                        "code": "METHOD_NOT_FOUND",
                        "message": f"Unknown method: {method}",
                    },
                }
            else:
                try:
                    result = await self._handlers[method](**params)
                    response = {"id": req_id, "result": result}
                except RPCError as e:
                    response = {
                        "id": req_id,
                        "error": {"code": e.code, "message": e.message},
                    }
                except Exception as e:
                    logger.exception("RPC handler error for %s", method)
                    response = {
                        "id": req_id,
                        "error": {"code": "INTERNAL_ERROR", "message": str(e)},
                    }

            # Send response
            writer.write(json.dumps(response).encode())
            await writer.drain()

        except Exception as e:
            logger.exception("RPC client handler error: %s", e)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class RPCClient:
    """Unix socket RPC client.

    Usage:
        client = RPCClient(Path("/tmp/daemon.sock"))
        result = await client.call("echo", msg="hello")
        # Or synchronously:
        result = client.call_sync("echo", msg="hello")
    """

    def __init__(self, socket_path: Path):
        """Initialize RPC client.

        Args:
            socket_path: Path to Unix socket file
        """
        self.socket_path = socket_path

    async def call(self, method: str, **params: Any) -> Any:
        """Call an RPC method asynchronously.

        Args:
            method: Method name to call
            **params: Method parameters

        Returns:
            Result from the RPC method

        Raises:
            RPCError: If the RPC returns an error
            ConnectionError: If cannot connect to daemon
        """
        try:
            reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        except (FileNotFoundError, ConnectionRefusedError) as e:
            raise ConnectionError(
                f"Cannot connect to daemon at {self.socket_path}: {e}"
            ) from e

        try:
            # Send request
            request = {
                "id": str(uuid.uuid4()),
                "method": method,
                "params": params,
            }
            writer.write(json.dumps(request).encode())
            await writer.drain()

            # Read response
            data = await reader.read(MAX_MESSAGE_SIZE)
            if not data:
                raise ConnectionError("Empty response from daemon")

            response = json.loads(data.decode())

            if "error" in response:
                error = response["error"]
                raise RPCError(error.get("code", "UNKNOWN"), error.get("message", ""))

            return response.get("result")

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def call_sync(self, method: str, **params: Any) -> Any:
        """Call an RPC method synchronously.

        This is a convenience wrapper for CLI commands.

        Args:
            method: Method name to call
            **params: Method parameters

        Returns:
            Result from the RPC method
        """
        return asyncio.run(self.call(method, **params))

    def is_daemon_running(self) -> bool:
        """Check if daemon is running by attempting to connect.

        Returns:
            True if daemon socket exists and is accepting connections
        """
        if not self.socket_path.exists():
            return False

        try:
            # Try to connect
            async def _check() -> bool:
                try:
                    _reader, writer = await asyncio.open_unix_connection(
                        str(self.socket_path)
                    )
                    writer.close()
                    await writer.wait_closed()
                    return True
                except (ConnectionRefusedError, FileNotFoundError):
                    return False

            return asyncio.run(_check())
        except Exception:
            return False


__all__ = [
    "RPCServer",
    "RPCClient",
    "RPCError",
]
