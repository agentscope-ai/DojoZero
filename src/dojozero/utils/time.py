"""Time and timezone utilities."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# US Eastern timezone for date conversion
US_EASTERN_TZ = ZoneInfo("America/New_York")


def utc_to_us_date(dt: datetime) -> str:
    """Convert a UTC datetime to US Eastern date string (YYYY-MM-DD).

    NBA/NFL games are scheduled in US local time, and external services like
    Polymarket use the US date in their identifiers/slugs. This function
    ensures consistent date handling across the codebase.

    Args:
        dt: A datetime object (assumed UTC if naive, otherwise uses its timezone)

    Returns:
        Date string in YYYY-MM-DD format, in US Eastern time
    """
    # Handle naive datetimes by assuming they are UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(US_EASTERN_TZ).strftime("%Y-%m-%d")


def utc_iso_to_local(utc_str: str, fmt: str = "%m-%d %H:%M") -> str:
    """Parse UTC ISO time string and convert to local timezone display.

    Args:
        utc_str: ISO format UTC time string (e.g., "2025-01-24T19:00:00+00:00")
        fmt: Output format string (default: "%m-%d %H:%M")

    Returns:
        Formatted time string in local timezone, or truncated input on error
    """
    if not utc_str:
        return ""
    try:
        utc_time = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        if utc_time.tzinfo is None:
            utc_time = utc_time.replace(tzinfo=timezone.utc)
        local_time = utc_time.astimezone()
        return local_time.strftime(fmt)
    except (ValueError, TypeError):
        return utc_str[:16]
