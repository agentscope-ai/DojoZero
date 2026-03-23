"""NCAA ExternalAPI implementation using ESPN API.

Wraps the generic ESPNExternalAPI with sport="basketball" and
league="mens-college-basketball" for NCAA Division I Men's Basketball.
"""

import logging
from typing import Any

from dojozero.data._stores import ExternalAPI
from dojozero.data.espn._api import ESPNExternalAPI

logger = logging.getLogger(__name__)


class NCAAExternalAPI(ExternalAPI):
    """ESPN NCAA Men's Basketball API implementation.

    Wraps the generic ESPNExternalAPI with sport="basketball" and
    league="mens-college-basketball".

    Endpoints:
    - scoreboard: Get all games for a date
    - summary: Get full game data by event_id (replaces boxscore)
    - plays: Get play-by-play data by event_id
    - teams: Get all NCAA teams

    Proxy support:
    - Set DOJOZERO_PROXY_URL environment variable to use a proxy
    """

    def __init__(self, timeout: int = 30, proxy: str | None = None):
        """Initialize NCAA API.

        Args:
            timeout: Request timeout in seconds
            proxy: Optional proxy URL.
        """
        super().__init__()
        self._api = ESPNExternalAPI(
            sport="basketball",
            league="mens-college-basketball",
            timeout=timeout,
            proxy=proxy,
        )

    async def fetch(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch data from ESPN NCAA API.

        Routes requests to the appropriate ESPN endpoint.

        Args:
            endpoint: API endpoint name
            params: Optional parameters

        Returns:
            Parsed JSON response
        """
        return await self._api.fetch(endpoint, params)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._api.close()


__all__ = ["NCAAExternalAPI"]
