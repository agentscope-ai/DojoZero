"""Time and timezone utilities."""

from datetime import datetime
from zoneinfo import ZoneInfo

# US Eastern timezone for date conversion
US_EASTERN_TZ = ZoneInfo("America/New_York")


def utc_to_us_date(dt: datetime) -> str:
    """Convert a UTC datetime to US Eastern date string (YYYY-MM-DD).

    NBA/NFL games are scheduled in US local time, and external services like
    Polymarket use the US date in their identifiers/slugs. This function
    ensures consistent date handling across the codebase.

    Args:
        dt: A datetime object (should be in UTC or timezone-aware)

    Returns:
        Date string in YYYY-MM-DD format, in US Eastern time
    """
    return dt.astimezone(US_EASTERN_TZ).strftime("%Y-%m-%d")
