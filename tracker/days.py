"""Local calendar days in a household's IANA time zone.

Instants are stored in UTC. These helpers only convert boundaries. A
spring-forward day is shorter than 24 hours and a fall-back day is longer;
both are `[start, end)` in UTC.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError


def validate_iana_timezone(value: str) -> None:
    """Reject abbreviations and unknown names. `UTC` and `America/Los_Angeles` pass."""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValidationError('Enter an IANA time zone name.')
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(
            'Enter a valid IANA time zone name (example: America/Los_Angeles).'
        ) from exc


def _zone(zone: str | ZoneInfo) -> ZoneInfo:
    if isinstance(zone, ZoneInfo):
        return zone
    if not isinstance(zone, str):
        raise TypeError('zone must be an IANA time zone name')
    try:
        ZoneInfo(zone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f'Unknown IANA time zone: {zone}') from exc
    return ZoneInfo(zone)


def _start_of_local_day(day: date, tz: ZoneInfo) -> datetime:
    """First UTC instant whose calendar date in `tz` is `day`.

    Second resolution is enough: civil offsets change on a minute boundary,
    and the search window covers the ±14 hour range of real zones. Dates
    that a zone skips entirely (a timezone reform that drops a calendar day)
    raise ValueError.
    """
    origin = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    lo = math.floor((origin - timedelta(hours=16)).timestamp())
    hi = math.floor((origin + timedelta(hours=40)).timestamp())
    while lo < hi:
        mid = (lo + hi) // 2
        local_day = datetime.fromtimestamp(mid, tz).date()
        if local_day < day:
            lo = mid + 1
        else:
            hi = mid
    start = datetime.fromtimestamp(lo, timezone.utc)
    if start.astimezone(tz).date() != day:
        raise ValueError(f'{day.isoformat()} does not exist in {tz.key}')
    return start


def local_day_bounds(day: date, zone: str | ZoneInfo) -> tuple[datetime, datetime]:
    """UTC interval `[start, end)` covering `day` in `zone`."""
    if not isinstance(day, date) or isinstance(day, datetime):
        raise TypeError('day must be a datetime.date')
    tz = _zone(zone)
    start = _start_of_local_day(day, tz)
    end = _start_of_local_day(day + timedelta(days=1), tz)
    return start, end


def local_date(instant: datetime, zone: str | ZoneInfo) -> date:
    """Calendar date of an aware instant in `zone`."""
    if (
        not isinstance(instant, datetime)
        or instant.tzinfo is None
        or instant.utcoffset() is None
    ):
        raise ValueError('instant must be timezone-aware')
    return instant.astimezone(_zone(zone)).date()
