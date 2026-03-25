"""Time and timezone utilities."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# US Eastern timezone for date conversion (default for US sports)
US_EASTERN_TZ = ZoneInfo("America/New_York")

# Cache for ZoneInfo objects to avoid repeated parsing
_TIMEZONE_CACHE: dict[str, ZoneInfo] = {"America/New_York": US_EASTERN_TZ}


def get_zoneinfo(tz_str: str) -> ZoneInfo:
    """Get a ZoneInfo object for the given timezone string.

    Args:
        tz_str: IANA timezone string (e.g., "America/New_York", "America/Los_Angeles")

    Returns:
        ZoneInfo object for the timezone. Falls back to US Eastern if invalid.
    """
    if not tz_str:
        return US_EASTERN_TZ
    if tz_str not in _TIMEZONE_CACHE:
        try:
            _TIMEZONE_CACHE[tz_str] = ZoneInfo(tz_str)
        except (KeyError, ValueError):
            # Invalid timezone string, fall back to Eastern
            _TIMEZONE_CACHE[tz_str] = US_EASTERN_TZ
    return _TIMEZONE_CACHE[tz_str]


def utc_to_local_date(dt: datetime, tz_str: str = "") -> str:
    """Convert a UTC datetime to a local date string (YYYY-MM-DD).

    Args:
        dt: A datetime object (assumed UTC if naive, otherwise uses its timezone)
        tz_str: IANA timezone string (e.g., "America/Los_Angeles").
                If empty, defaults to US Eastern.

    Returns:
        Date string in YYYY-MM-DD format, in the specified timezone
    """
    # Handle naive datetimes by assuming they are UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz = get_zoneinfo(tz_str) if tz_str else US_EASTERN_TZ
    return dt.astimezone(tz).strftime("%Y-%m-%d")


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
    return utc_to_local_date(dt, "")


def us_game_day_today() -> str:
    """Calendar date string for current US Eastern game day."""
    return utc_to_us_date(datetime.now(timezone.utc))


def us_game_day_today_and_yesterday() -> tuple[str, str]:
    """Current and previous US Eastern game day date strings."""
    today = us_game_day_today()
    d = datetime.strptime(today, "%Y-%m-%d").date()
    yesterday = (d - timedelta(days=1)).isoformat()
    return today, yesterday


def utc_iso_to_local_date(utc_str: str, tz_str: str = "") -> str:
    """Convert a UTC ISO timestamp string to a local date string (YYYY-MM-DD).

    Args:
        utc_str: ISO format UTC time string (e.g., "2025-01-24T19:00:00Z")
        tz_str: IANA timezone string (e.g., "America/Los_Angeles").
                If empty, defaults to US Eastern.

    Returns:
        Date string in YYYY-MM-DD format, in the specified timezone.
        Returns empty string on error.
    """
    if not utc_str:
        return ""
    try:
        utc_time = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return utc_to_local_date(utc_time, tz_str)
    except (ValueError, TypeError):
        # Fallback: take first 10 chars if it looks like a date
        return utc_str[:10] if len(utc_str) >= 10 else ""


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
