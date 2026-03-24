"""NCAA ExternalAPI implementation using ESPN API.

Extends NBAExternalAPI with sport="basketball" and
league="mens-college-basketball" for NCAA Division I Men's Basketball.

Since NCAA basketball uses the same ESPN data format as NBA, we reuse
NBAExternalAPI's endpoint mapping (boxscore → summary, play_by_play → plays)
and only override the league parameter.
"""

from dojozero.data.nba._api import NBAExternalAPI


class NCAAExternalAPI(NBAExternalAPI):
    """ESPN NCAA Men's Basketball API implementation.

    Inherits all endpoint mapping from NBAExternalAPI. Only overrides
    the ESPN league to "mens-college-basketball".
    """

    def __init__(self, timeout: int = 30, proxy: str | None = None):
        super().__init__(timeout=timeout, proxy=proxy)
        # Re-create the underlying ESPN API with NCAA league
        from dojozero.data.espn._api import ESPNExternalAPI

        self._api = ESPNExternalAPI(
            sport="basketball",
            league="mens-college-basketball",
            timeout=timeout,
            proxy=proxy,
        )


__all__ = ["NCAAExternalAPI"]
