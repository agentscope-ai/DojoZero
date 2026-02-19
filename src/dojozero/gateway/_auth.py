"""Authentication for Agent Gateway.

Provides JWT-based authentication for external agents.
Phase 1-2: Simple X-Agent-ID header
Phase 3: Full JWT validation with RSA-256
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import Header, HTTPException

from dojozero.gateway._models import ErrorCodes, ErrorDetail, ErrorResponse

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


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
    "AuthConfig",
    "AuthProvider",
    "create_auth_dependency",
]
