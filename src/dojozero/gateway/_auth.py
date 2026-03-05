"""Authentication for Agent Gateway.

Provides authentication for external agents:
- API key validation against external identity service (or local config)
- JWT-based authentication
- Simple X-Agent-ID header (development/testing)

The AgentAuthenticator protocol allows plugging in different backends:
- LocalAgentAuthenticator: File/config-based for development
- ExternalAgentAuthenticator: Calls external identity service (future)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml
from fastapi import Header, HTTPException

from dojozero.gateway._models import ErrorCodes, ErrorDetail, ErrorResponse

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Agent Identity (returned by authenticators)
# =============================================================================


@dataclass(slots=True, frozen=True)
class AgentIdentity:
    """Verified agent identity from authentication.

    This is returned by AgentAuthenticator.validate() and represents
    the canonical identity for cross-trial aggregation.

    Includes display metadata for frontend rendering (persona, model, avatar).
    """

    agent_id: str  # Canonical ID for aggregation (from identity service)
    display_name: str | None = None  # Human-readable name
    persona: str | None = None  # Persona tag (e.g., "degen", "whale", "shark")
    model: str | None = None  # Exact model name (e.g., "gpt-4", "qwen3-max")
    model_display_name: str | None = None  # Human-readable model name
    cdn_url: str | None = None  # Avatar image URL
    metadata: dict[str, Any] | None = None  # Additional info (org, tier, etc.)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (camelCase for API compatibility)."""
        return {
            "agentId": self.agent_id,
            "displayName": self.display_name,
            "persona": self.persona,
            "model": self.model,
            "modelDisplayName": self.model_display_name,
            "cdnUrl": self.cdn_url,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentIdentity:
        """Create from dictionary (supports both camelCase and snake_case)."""
        return cls(
            agent_id=data.get("agentId") or data.get("agent_id", ""),
            display_name=data.get("displayName") or data.get("display_name"),
            persona=data.get("persona"),
            model=data.get("model"),
            model_display_name=data.get("modelDisplayName")
            or data.get("model_display_name"),
            cdn_url=data.get("cdnUrl") or data.get("cdn_url"),
            metadata=data.get("metadata"),
        )


# =============================================================================
# Authenticator Protocol
# =============================================================================


class AgentAuthenticator(Protocol):
    """Protocol for agent authentication.

    Implementations validate API keys and return verified agent identity.
    This allows swapping between local config and external identity service.
    """

    async def validate(self, api_key: str) -> AgentIdentity | None:
        """Validate API key and return agent identity.

        Args:
            api_key: The API key to validate

        Returns:
            AgentIdentity if valid, None if invalid/unknown key
        """
        ...

    def is_enabled(self) -> bool:
        """Check if this authenticator is enabled/configured."""
        ...


# =============================================================================
# Agent Key Manager (YAML file management)
# =============================================================================


@dataclass
class AgentKeyEntry:
    """Entry representing an agent key and its associated identity.

    YAML format examples:

    Simple format (just agent_id):
        sk-agent-abc123: my-agent

    Full format with metadata:
        sk-agent-abc123:
          agent_id: my-agent
          display_name: My Agent
          persona: degen
          model: gpt-4
          model_display_name: GPT-4
          cdn_url: https://example.com/avatar.png
    """

    api_key: str
    identity: AgentIdentity

    @classmethod
    def from_yaml_data(cls, api_key: str, data: dict | str) -> AgentKeyEntry:
        """Create from YAML data (supports both simple and full format)."""
        if isinstance(data, str):
            # Simple format: api_key: agent_id
            identity = AgentIdentity(agent_id=data)
        else:
            # Full format with dict
            identity = AgentIdentity(
                agent_id=data.get("agent_id", api_key),
                display_name=data.get("display_name"),
                persona=data.get("persona"),
                model=data.get("model"),
                model_display_name=data.get("model_display_name"),
                cdn_url=data.get("cdn_url"),
                metadata=data.get("metadata"),
            )
        return cls(api_key=api_key, identity=identity)

    def to_yaml_data(self) -> dict:
        """Convert to YAML-compatible dict."""
        data: dict[str, Any] = {"agent_id": self.identity.agent_id}
        if self.identity.display_name:
            data["display_name"] = self.identity.display_name
        if self.identity.persona:
            data["persona"] = self.identity.persona
        if self.identity.model:
            data["model"] = self.identity.model
        if self.identity.model_display_name:
            data["model_display_name"] = self.identity.model_display_name
        if self.identity.cdn_url:
            data["cdn_url"] = self.identity.cdn_url
        if self.identity.metadata:
            data["metadata"] = self.identity.metadata
        return data


class AgentKeyManager:
    """Manages agent API keys in YAML file.

    Provides a clean interface for loading, saving, and managing agent keys.
    Used by both CLI (`dojo0 agents` commands) and LocalAgentAuthenticator.

    YAML format:
    ```yaml
    agents:
      sk-agent-abc123:
        agent_id: agent_alice
        display_name: Alice's Agent
        metadata:
          org: team-alpha
      sk-agent-def456: simple_agent_id  # Simple format
    ```
    """

    def __init__(self, keys_file: Path | str):
        """Initialize key manager.

        Args:
            keys_file: Path to agent_keys.yaml file
        """
        self._keys_file = Path(keys_file)
        self._entries: dict[str, AgentKeyEntry] = {}
        self._last_mtime: float = 0.0
        self._load()

    def _load(self) -> None:
        """Load keys from YAML file."""
        if not self._keys_file.exists():
            self._last_mtime = 0.0
            return

        try:
            # Track file modification time for auto-reload
            self._last_mtime = self._keys_file.stat().st_mtime

            with open(self._keys_file) as f:
                data = yaml.safe_load(f) or {}

            agents = data.get("agents", {})
            for api_key, agent_data in agents.items():
                self._entries[api_key] = AgentKeyEntry.from_yaml_data(
                    api_key, agent_data
                )

            logger.debug(
                "Loaded %d agent keys from %s", len(self._entries), self._keys_file
            )
        except Exception as e:
            logger.error("Failed to load agent keys from %s: %s", self._keys_file, e)

    def _check_reload(self) -> None:
        """Reload if file has been modified since last load."""
        if not self._keys_file.exists():
            if self._entries:
                # File was deleted, clear entries
                self._entries.clear()
                self._last_mtime = 0.0
            return

        try:
            current_mtime = self._keys_file.stat().st_mtime
            if current_mtime > self._last_mtime:
                logger.info("Agent keys file modified, reloading...")
                self._entries.clear()
                self._load()
        except OSError:
            pass  # File access error, skip reload

    def _save(self) -> None:
        """Save keys to YAML file."""
        # Ensure parent directory exists
        self._keys_file.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "agents": {
                api_key: entry.to_yaml_data()
                for api_key, entry in self._entries.items()
            }
        }

        with open(self._keys_file, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

    def reload(self) -> None:
        """Reload keys from file."""
        self._entries.clear()
        self._load()

    @property
    def keys_file(self) -> Path:
        """Return path to keys file."""
        return self._keys_file

    def get(self, api_key: str) -> AgentIdentity | None:
        """Get identity for API key.

        Auto-reloads from file if it has been modified.

        Args:
            api_key: The API key to look up

        Returns:
            AgentIdentity if found, None otherwise
        """
        self._check_reload()
        entry = self._entries.get(api_key)
        return entry.identity if entry else None

    def find_by_agent_id(self, agent_id: str) -> AgentKeyEntry | None:
        """Find entry by agent_id.

        Args:
            agent_id: The agent ID to search for

        Returns:
            AgentKeyEntry if found, None otherwise
        """
        for entry in self._entries.values():
            if entry.identity.agent_id == agent_id:
                return entry
        return None

    def add(
        self,
        api_key: str,
        agent_id: str,
        display_name: str | None = None,
        persona: str | None = None,
        model: str | None = None,
        model_display_name: str | None = None,
        cdn_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentKeyEntry:
        """Add a new agent key.

        Args:
            api_key: The API key
            agent_id: The agent identifier
            display_name: Optional human-readable name
            persona: Optional persona tag (e.g., 'degen', 'whale')
            model: Optional model identifier (e.g., 'gpt-4')
            model_display_name: Optional human-readable model name
            cdn_url: Optional avatar image URL
            metadata: Optional additional metadata

        Returns:
            The created AgentKeyEntry
        """
        identity = AgentIdentity(
            agent_id=agent_id,
            display_name=display_name,
            persona=persona,
            model=model,
            model_display_name=model_display_name,
            cdn_url=cdn_url,
            metadata=metadata,
        )
        entry = AgentKeyEntry(api_key=api_key, identity=identity)
        self._entries[api_key] = entry
        self._save()
        return entry

    def remove(self, api_key: str) -> bool:
        """Remove an agent key.

        Args:
            api_key: The API key to remove

        Returns:
            True if removed, False if not found
        """
        if api_key in self._entries:
            del self._entries[api_key]
            self._save()
            return True
        return False

    def remove_by_agent_id(self, agent_id: str) -> bool:
        """Remove agent by agent_id.

        Args:
            agent_id: The agent ID to remove

        Returns:
            True if removed, False if not found
        """
        entry = self.find_by_agent_id(agent_id)
        if entry:
            return self.remove(entry.api_key)
        return False

    def list_all(self) -> list[AgentKeyEntry]:
        """List all agent key entries.

        Returns:
            List of all AgentKeyEntry objects
        """
        return list(self._entries.values())

    def __len__(self) -> int:
        """Return number of registered keys."""
        return len(self._entries)

    def __bool__(self) -> bool:
        """Return True if any keys are registered."""
        return len(self._entries) > 0

    def to_identity_dict(self) -> dict[str, AgentIdentity]:
        """Convert to dict mapping api_key -> AgentIdentity.

        Useful for passing to LocalAgentAuthenticator.
        """
        return {api_key: entry.identity for api_key, entry in self._entries.items()}

    @staticmethod
    def generate_api_key() -> str:
        """Generate a new API key.

        Returns:
            A new unique API key string
        """
        import secrets

        return f"sk-agent-{secrets.token_hex(16)}"


# =============================================================================
# Local Authenticator (for development)
# =============================================================================


class LocalAgentAuthenticator:
    """Simple file/config-based authenticator for development.

    Reads agent keys from a YAML config file:
    ```yaml
    agents:
      sk-agent-abc123:
        agent_id: agent_alice
        display_name: Alice's Agent
        metadata:
          org: team-alpha
      sk-agent-def456:
        agent_id: agent_bob
        display_name: Bob's Agent
    ```

    Can be initialized with:
    - Direct keys dict (for testing)
    - Config file path (uses AgentKeyManager internally)
    - AgentKeyManager instance (for sharing with CLI)
    """

    def __init__(
        self,
        keys: dict[str, AgentIdentity] | None = None,
        config_path: Path | str | None = None,
        key_manager: AgentKeyManager | None = None,
    ):
        """Initialize local authenticator.

        Args:
            keys: Direct mapping of api_key -> AgentIdentity
            config_path: Path to YAML config file
            key_manager: Optional AgentKeyManager instance to use
        """
        self._keys: dict[str, AgentIdentity] = keys or {}
        self._key_manager: AgentKeyManager | None = key_manager

        # If config_path provided but no key_manager, create one
        if config_path and not key_manager:
            self._key_manager = AgentKeyManager(config_path)
            # Copy keys from manager to local dict
            self._keys.update(self._key_manager.to_identity_dict())
        elif key_manager:
            # Use provided key_manager
            self._keys.update(key_manager.to_identity_dict())

        logger.info("LocalAgentAuthenticator initialized with %d keys", len(self._keys))

    async def validate(self, api_key: str) -> AgentIdentity | None:
        """Validate API key and return agent identity."""
        # Check local keys first
        if api_key in self._keys:
            return self._keys[api_key]
        # If we have a key_manager, check there (may have been updated)
        if self._key_manager:
            return self._key_manager.get(api_key)
        return None

    def is_enabled(self) -> bool:
        """Check if authenticator has any keys configured."""
        if self._key_manager:
            return bool(self._key_manager)
        return len(self._keys) > 0

    def add_key(self, api_key: str, identity: AgentIdentity) -> None:
        """Add a key (for testing)."""
        self._keys[api_key] = identity

    def remove_key(self, api_key: str) -> bool:
        """Remove a key."""
        if api_key in self._keys:
            del self._keys[api_key]
            return True
        return False

    def reload(self) -> None:
        """Reload keys from file (if using key_manager)."""
        if self._key_manager:
            self._key_manager.reload()
            self._keys.update(self._key_manager.to_identity_dict())


# =============================================================================
# No-op Authenticator (allows all, for backwards compatibility)
# =============================================================================


class NoOpAuthenticator:
    """Authenticator that allows any API key (no validation).

    Used when authentication is disabled for backwards compatibility and testing.
    The api_key is used as both agent_id and display_name in the returned identity.
    """

    async def validate(self, api_key: str) -> AgentIdentity | None:
        """Return identity using api_key as agent_id (no actual validation)."""
        if not api_key:
            return None
        return AgentIdentity(agent_id=api_key, display_name=api_key)

    def is_enabled(self) -> bool:
        """Always returns False (no actual authentication)."""
        return False


@dataclass
class AgentCredentials:
    """Validated agent credentials from auth token or header."""

    agent_id: str
    trial_id: str | None = None
    issued_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_expired(self) -> bool:
        """Check if credentials have expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


@dataclass
class AuthConfig:
    """Configuration for authentication."""

    # JWT settings (Phase 3)
    jwt_secret: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "dojozero"
    token_expiry_seconds: int = 3600

    # Simple auth settings (Phase 1-2)
    require_registration: bool = True
    allow_header_auth: bool = True


class AuthProvider:
    """Handles authentication for the Gateway.

    Supports multiple auth methods:
    - X-Agent-ID header (Phase 1-2, simple)
    - JWT Bearer token (Phase 3, secure)
    """

    def __init__(self, config: AuthConfig | None = None):
        """Initialize auth provider.

        Args:
            config: Auth configuration. Uses defaults if None.
        """
        self.config = config or AuthConfig()
        self._registered_agents: set[str] = set()

        logger.info(
            "AuthProvider initialized: header_auth=%s, jwt=%s",
            self.config.allow_header_auth,
            self.config.jwt_secret is not None,
        )

    def register_agent(self, agent_id: str) -> None:
        """Mark an agent as registered."""
        self._registered_agents.add(agent_id)
        logger.debug("Agent registered for auth: %s", agent_id)

    def unregister_agent(self, agent_id: str) -> None:
        """Remove agent from registered set."""
        self._registered_agents.discard(agent_id)
        logger.debug("Agent unregistered from auth: %s", agent_id)

    def is_registered(self, agent_id: str) -> bool:
        """Check if agent is registered."""
        return agent_id in self._registered_agents

    def validate_header_auth(self, agent_id: str) -> AgentCredentials:
        """Validate X-Agent-ID header authentication.

        Args:
            agent_id: Agent ID from header

        Returns:
            Validated credentials

        Raises:
            HTTPException: If validation fails
        """
        if not agent_id:
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.AUTH_REQUIRED,
                        message="X-Agent-ID header required",
                    )
                ).model_dump(by_alias=True),
            )

        return AgentCredentials(agent_id=agent_id)

    def validate_jwt(self, token: str) -> AgentCredentials:
        """Validate JWT Bearer token.

        Args:
            token: JWT token string

        Returns:
            Validated credentials

        Raises:
            HTTPException: If validation fails
        """
        if not self.config.jwt_secret:
            raise HTTPException(
                status_code=500,
                detail="JWT authentication not configured",
            )

        try:
            # Import jwt only when needed
            import jwt

            payload = jwt.decode(
                token,
                self.config.jwt_secret,
                algorithms=[self.config.jwt_algorithm],
                issuer=self.config.jwt_issuer,
            )

            agent_id = payload.get("sub")
            if not agent_id:
                raise HTTPException(
                    status_code=401,
                    detail=ErrorResponse(
                        error=ErrorDetail(
                            code=ErrorCodes.INVALID_TOKEN,
                            message="Token missing subject claim",
                        )
                    ).model_dump(by_alias=True),
                )

            return AgentCredentials(
                agent_id=agent_id,
                trial_id=payload.get("trial_id"),
                issued_at=payload.get("iat", time.time()),
                expires_at=payload.get("exp"),
                scopes=frozenset(payload.get("scopes", [])),
            )

        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.TOKEN_EXPIRED,
                        message="Token has expired",
                    )
                ).model_dump(by_alias=True),
            )
        except jwt.InvalidTokenError as e:
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCodes.INVALID_TOKEN,
                        message=f"Invalid token: {e}",
                    )
                ).model_dump(by_alias=True),
            )

    def create_token(
        self,
        agent_id: str,
        trial_id: str | None = None,
        scopes: list[str] | None = None,
    ) -> str:
        """Create a JWT token for an agent.

        Args:
            agent_id: Agent identifier
            trial_id: Optional trial ID to bind token to
            scopes: Optional list of scopes

        Returns:
            JWT token string

        Raises:
            ValueError: If JWT not configured
        """
        if not self.config.jwt_secret:
            raise ValueError("JWT authentication not configured")

        import jwt

        now = time.time()
        payload: dict[str, Any] = {
            "sub": agent_id,
            "iss": self.config.jwt_issuer,
            "iat": now,
            "exp": now + self.config.token_expiry_seconds,
        }

        if trial_id:
            payload["trial_id"] = trial_id
        if scopes:
            payload["scopes"] = scopes

        return jwt.encode(
            payload,
            self.config.jwt_secret,
            algorithm=self.config.jwt_algorithm,
        )

    def authenticate(
        self,
        x_agent_id: str | None = None,
        authorization: str | None = None,
    ) -> AgentCredentials:
        """Authenticate a request using available credentials.

        Tries JWT first if available, falls back to X-Agent-ID header.

        Args:
            x_agent_id: Value from X-Agent-ID header
            authorization: Value from Authorization header

        Returns:
            Validated credentials

        Raises:
            HTTPException: If authentication fails
        """
        # Try JWT Bearer token first
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]  # Remove "Bearer " prefix
            if self.config.jwt_secret:
                return self.validate_jwt(token)

        # Fall back to X-Agent-ID header
        if self.config.allow_header_auth and x_agent_id:
            return self.validate_header_auth(x_agent_id)

        # No valid auth provided
        raise HTTPException(
            status_code=401,
            detail=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCodes.AUTH_REQUIRED,
                    message="Authentication required. Provide X-Agent-ID header or Bearer token.",
                )
            ).model_dump(by_alias=True),
        )


def create_auth_dependency(auth_provider: AuthProvider):
    """Create a FastAPI dependency for authentication.

    Args:
        auth_provider: AuthProvider instance

    Returns:
        Dependency function that returns AgentCredentials
    """

    def get_credentials(
        x_agent_id: str | None = Header(default=None, alias="X-Agent-ID"),
        authorization: str | None = Header(default=None),
    ) -> AgentCredentials:
        """FastAPI dependency to get authenticated agent credentials."""
        return auth_provider.authenticate(
            x_agent_id=x_agent_id,
            authorization=authorization,
        )

    return get_credentials


__all__ = [
    "AgentCredentials",
    "AgentIdentity",
    "AgentAuthenticator",
    "AgentKeyEntry",
    "AgentKeyManager",
    "AuthConfig",
    "AuthProvider",
    "LocalAgentAuthenticator",
    "NoOpAuthenticator",
    "create_auth_dependency",
]
