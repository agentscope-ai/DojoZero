"""NFL ExternalAPI implementation using generic ESPN API."""

from typing import Any

from dojozero.data._stores import ExternalAPI
from dojozero.data.espn import ESPNExternalAPI


class NFLExternalAPI(ExternalAPI):
    """ESPN NFL API implementation.

    Wraps the generic ESPNExternalAPI with sport="football" and league="nfl".

    Endpoints:
    - scoreboard: Get all games for a date/week
    - summary: Get full game data by event_id
    - plays: Get play-by-play data by event_id
    - teams: Get all NFL teams

    Proxy support:
    - Set DOJOZERO_PROXY_URL environment variable to use a proxy
    - Example: export DOJOZERO_PROXY_URL="http://proxy.example.com:8080"
    """

    def __init__(self, timeout: int = 30, proxy: str | None = None):
        """Initialize NFL API.

        Args:
            timeout: Request timeout in seconds
            proxy: Optional proxy URL. If not provided, will use DOJOZERO_PROXY_URL env var
        """
        super().__init__()
        self._api = ESPNExternalAPI(
            sport="football",
            league="nfl",
            timeout=timeout,
            proxy=proxy,
        )

    async def fetch(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch NFL data from ESPN API.

        Args:
            endpoint: API endpoint ("scoreboard", "summary", "plays", "teams")
            params: Request parameters (varies by endpoint)

        Returns:
            API response as dict
        """
        return await self._api.fetch(endpoint, params)

    async def close(self) -> None:
        """Close the underlying API session."""
        await self._api.close()
