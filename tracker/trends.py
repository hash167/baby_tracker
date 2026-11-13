"""Per-day trends in the household timezone.

Each local date is one bucket, including days with no events. A feed with
several segments counts once. Volumes are millilitres. Nothing here is logged.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.utils import timezone

from tracker.days import local_date, local_day_bounds
from tracker.feeds import display_totals
from tracker.models import Event
from tracker.nara import ML_PER_FLOZ

TREND_LENGTHS = (7, 14, 30)
CHART_HEIGHT = 100
CHART_WIDTH = 320

DIAPER_COLORS = (
    ('wet', '#0b4f8a'),
    ('dirty', '#6a3412'),
    ('both', '#5a276c'),
    ('dry', '#8aa0b4'),
)


def trend_length(value) -> int:
    try:
        days = int(value)
    except TypeError, ValueError:
        return 7
    if days not in TREND_LENGTHS:
        return 7
    return days


def trend_dates(household, days: int, *, now=None) -> list[date]:
    """The `days` local dates ending today, oldest first."""
    if days not in TREND_LENGTHS:
        raise ValueError(days)
    today = local_date(now or timezone.now(), household.timezone)
    start = today - timedelta(days=days - 1)
    return [start + timedelta(days=offset) for offset in range(days)]


def previous_dates(dates: list[date]) -> list[date]:
    """The equal run of local dates immediately before `dates`."""
    count = len(dates)
    start = dates[0] - timedelta(days=count)
    return [start + timedelta(days=offset) for offset in range(count)]


def empty_trends(household, days: int, *, now=None) -> dict:
    """Zero-filled range when the household has no baby yet."""
    now = now or timezone.now()
    current = trend_dates(household, days, now=now)
    previous = previous_dates(current)
    current_rows = [_blank(day) for day in current]
    previous_rows = [_blank(day) for day in previous]
    report = _report(days, current_rows, previous_rows, feed_gap_seconds=None)
    return report


def build_trends(baby, days: int, *, now=None) -> dict:
    now = now or timezone.now()
    current = trend_dates(baby.household, days, now=now)
    previous = previous_dates(current)
    buckets = {day: _blank(day) for day in previous + current}
    start, _end_of_first = local_day_bounds(previous[0], baby.household.timezone)
    _start_of_last, end = local_day_bounds(current[-1], baby.household.timezone)
    events = (
        Event.objects.filter(
            baby=baby,
            household=baby.household,
            occurred_at__gte=start,
            occurred_at__lt=end,
        )
        .prefetch_related('segments')
        .order_by('occurred_at')
    )
    seen = set()
    current_feeds = []
    for event in events:
        if event.pk in seen:
            continue
        seen.add(event.pk)
        day = local_date(event.occurred_at, baby.household.timezone)
        bucket = buckets.get(day)
        if bucket is None:
            continue
        _add(bucket, event)
        if day in _as_set(current) and event.kind in (
            Event.Kind.FEED,
            Event.Kind.BOTTLE,
        ):
            current_feeds.append(event)
    current_rows = [buckets[day] for day in current]
    previous_rows = [buckets[day] for day in previous]
    return _report(days, current_rows, previous_rows, _average_gap(current_feeds))


def _report(days, current_rows, previous_rows, feed_gap_seconds) -> dict:
    return {
        'days': days,
        'label': f'Last {days} days',
        'previous_label': f'previous {days} days',
        'rows': current_rows,
        'headlines': _headlines(current_rows, previous_rows),
        'feed_gap_seconds': feed_gap_seconds,
        'charts': {
            'diapers': _stacked(current_rows, DIAPER_COLORS),
            'feeds': _single(current_rows, 'feeds', '#1f6f6a'),
            'nursing': _stacked(
                current_rows,
                (('left_minutes', '#1f6f6a'), ('right_minutes', '#c4a36a')),
            ),
            'bottles': _single(current_rows, 'bottles', '#1f6f6a'),
            'bottle_ml': _single(current_rows, 'bottle_ml', '#0b4f8a'),
            'pumps': _single(current_rows, 'pumps', '#5a276c'),
            'pump_ml': _single(current_rows, 'pump_ml', '#6a3412'),
            'sleep': _single(current_rows, 'sleep_hours', '#243044'),
        },
    }


def _as_set(dates: list[date]) -> set[date]:
    return set(dates)


def _blank(day: date) -> dict:
    return {
        'date': day,
        'label': f'{day.strftime("%b")} {day.day}',
        'wet': 0,
        'dirty': 0,
        'both': 0,
        'dry': 0,
        'feeds': 0,
        'left_minutes': 0.0,
        'right_minutes': 0.0,
        'bottles': 0,
        'bottle_ml': 0.0,
        'pumps': 0,
        'pump_ml': 0.0,
        'sleep_hours': 0.0,
    }


def _add(bucket: dict, event) -> None:
    if event.kind == Event.Kind.DIAPER:
        if event.diaper_type in {'wet', 'dirty', 'both', 'dry'}:
            bucket[event.diaper_type] += 1
        return
    if event.kind == Event.Kind.FEED:
        bucket['feeds'] += 1
        left, right = display_totals(event)
        bucket['left_minutes'] += _minutes(left)
        bucket['right_minutes'] += _minutes(right)
        return
    if event.kind == Event.Kind.BOTTLE:
        bucket['bottles'] += 1
        bucket['bottle_ml'] += _volume_ml(event.archive)
        return
    if event.kind == Event.Kind.PUMP:
        bucket['pumps'] += 1
        bucket['pump_ml'] += _volume_ml(event.archive)
        return
    if event.kind == Event.Kind.SLEEP:
        bucket['sleep_hours'] += _sleep_hours(event)


def _minutes(seconds) -> float:
    if seconds is None:
        return 0.0
    return max(float(seconds), 0) / 60


def _volume_ml(archive) -> float:
    """Millilitres already stored, or one conversion from a floz measurement.

    `volume_ml` wins, so a total and its parts are not added together.
    """
    if not archive:
        return 0.0
    stored = archive.get('volume_ml')
    if stored is not None and stored != '':
        return max(float(stored), 0)
    for key in ('volume', 'total'):
        item = archive.get(key)
        converted = _measurement_ml(item)
        if converted is not None:
            return converted
    parts = []
    for key in ('breast_milk', 'formula', 'left', 'right'):
        converted = _measurement_ml((archive or {}).get(key))
        if converted is not None:
            parts.append(converted)
    return float(sum(parts))


def _measurement_ml(item) -> float | None:
    if not item or item.get('amount') in (None, ''):
        return None
    if item.get('ml') is not None:
        return max(float(item['ml']), 0)
    try:
        amount = float(item['amount'])
    except TypeError, ValueError:
        return None
    unit = (item.get('unit') or '').strip().lower()
    if unit == 'ml':
        return max(amount, 0)
    if unit == 'floz':
        return max(amount, 0) * ML_PER_FLOZ
    return None


def _sleep_hours(event) -> float:
    archive = event.archive or {}
    raw = archive.get('duration_seconds')
    if raw not in (None, ''):
        try:
            return max(float(raw), 0) / 3600
        except TypeError, ValueError:
            return 0.0
    ended = archive.get('ended_at')
    if not ended or event.occurred_at is None:
        return 0.0
    try:
        end = datetime.fromisoformat(ended)
    except ValueError:
        return 0.0
    if end.tzinfo is None or end.utcoffset() is None:
        return 0.0
    return max((end - event.occurred_at).total_seconds(), 0) / 3600


def _average_gap(events) -> float | None:
    times = sorted(event.occurred_at for event in events)
    if len(times) < 2:
        return None
    gaps = [
        (later - earlier).total_seconds() for earlier, later in zip(times, times[1:])
    ]
    return sum(gaps) / len(gaps)


def _sum(rows: list[dict], *keys: str) -> float:
    total = 0.0
    for row in rows:
        for key in keys:
            total += row[key]
    return total


def _daily(current: float, previous: float, days: int) -> dict:
    current_avg = current / days
    previous_avg = previous / days
    return {
        'current': current_avg,
        'previous': previous_avg,
        'change': current_avg - previous_avg,
    }


def _headlines(current: list[dict], previous: list[dict]) -> dict:
    days = len(current)
    return {
        'diapers_per_day': _daily(
            _sum(current, 'wet', 'dirty', 'both', 'dry'),
            _sum(previous, 'wet', 'dirty', 'both', 'dry'),
            days,
        ),
        'nursing_minutes_per_day': _daily(
            _sum(current, 'left_minutes', 'right_minutes'),
            _sum(previous, 'left_minutes', 'right_minutes'),
            days,
        ),
        'bottle_ml_per_day': _daily(
            _sum(current, 'bottle_ml'),
            _sum(previous, 'bottle_ml'),
            days,
        ),
        'pump_ml_per_day': _daily(
            _sum(current, 'pump_ml'),
            _sum(previous, 'pump_ml'),
            days,
        ),
        'sleep_hours_per_day': _daily(
            _sum(current, 'sleep_hours'),
            _sum(previous, 'sleep_hours'),
            days,
        ),
    }


def _stacked(rows: list[dict], series: tuple) -> list[dict]:
    peak = 0.0
    for row in rows:
        peak = max(peak, sum(float(row[name]) for name, _color in series))
    if peak <= 0:
        peak = 1.0
    slot = CHART_WIDTH / len(rows)
    bar_width = slot * 0.72
    charts = []
    for index, row in enumerate(rows):
        x = index * slot + (slot - bar_width) / 2
        cursor = float(CHART_HEIGHT)
        segments = []
        for name, color in series:
            value = float(row[name])
            height = value / peak * (CHART_HEIGHT - 4)
            cursor -= height
            segments.append(
                {
                    'name': name,
                    'color': color,
                    'x': round(x, 2),
                    'y': round(cursor, 2),
                    'width': round(bar_width, 2),
                    'height': round(height, 2),
                }
            )
        charts.append({'label': row['label'], 'segments': segments})
    return charts


def _single(rows: list[dict], key: str, color: str) -> list[dict]:
    return _stacked(rows, ((key, color),))
