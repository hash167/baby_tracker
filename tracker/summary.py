"""Local-day and rolling-range timeline totals.

A feed that crosses local midnight counts on the day it started.
Stored UTC instants do not change when the household timezone changes.

A range is a rolling window that ends at the current instant. The end and
the start are expressed in the household timezone, then compared to the
stored UTC instants. 24 hours, 48 hours, and 7 days are durations, so a
spring-forward or fall-back does not shorten or lengthen the window.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from tracker.feeds import current_side, display_totals, open_feed
from tracker.models import Event
from tracker.policy import VISIBLE_KINDS

RANGE_WINDOWS = {
    '24h': timedelta(hours=24),
    '48h': timedelta(hours=48),
    '7d': timedelta(days=7),
}
RANGE_LABELS = {
    '24h': 'Last 24 hours',
    '48h': 'Last 48 hours',
    '7d': 'Last 7 days',
}
RANGE_LINKS = (
    ('24h', '24 hours'),
    ('48h', '48 hours'),
    ('7d', '7 days'),
)
OTHER_KIND_LABELS = {
    Event.Kind.SLEEP: 'Sleep',
    Event.Kind.GROWTH: 'Growth',
    Event.Kind.MEDICAL: 'Medical',
    Event.Kind.UNKNOWN: 'Unknown',
    Event.Kind.EVENT: 'Event',
}


def events_on_day(baby, day: date, *, kind: str | None = None):
    start, end = baby.household.local_day_bounds(day)
    kinds = (kind,) if kind else VISIBLE_KINDS
    return (
        Event.objects.filter(
            baby=baby,
            kind__in=kinds,
            occurred_at__gte=start,
            occurred_at__lt=end,
        )
        .select_related('created_by')
        .order_by('-occurred_at', '-recorded_at')
    )


def group_local_days(household, events):
    buckets: dict[date, list] = defaultdict(list)
    for event in events:
        buckets[household.local_date(event.occurred_at)].append(event)
    return dict(buckets)


def diaper_counts(events) -> dict[str, int]:
    counts = {choice: 0 for choice, _label in Event.DiaperType.choices}
    for event in events:
        if event.kind == Event.Kind.DIAPER and event.diaper_type in counts:
            counts[event.diaper_type] += 1
    return counts


def day_feed_totals(events) -> tuple[int, int, int]:
    left = 0
    right = 0
    count = 0
    for event in events:
        if event.kind != Event.Kind.FEED:
            continue
        event_left, event_right = display_totals(event)
        left += event_left or 0
        right += event_right or 0
        count += 1
    return left, right, count


def range_bounds(household, key: str, *, now=None) -> tuple[datetime, datetime]:
    """UTC interval `[start, end]` for a rolling window ending now.

    `end` is the current instant. `start` is that instant minus the window,
    computed after converting now into the household timezone.
    """
    if key not in RANGE_WINDOWS:
        raise ValueError(key)
    now = now or timezone.now()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    local_now = now.astimezone(ZoneInfo(household.timezone))
    local_start = local_now - RANGE_WINDOWS[key]
    return (
        local_start.astimezone(timezone.UTC),
        local_now.astimezone(timezone.UTC),
    )


def events_in_range(baby, key: str, *, now=None):
    """Visible and imported rows for one baby whose start falls in the window.

    One row is one event. Feed segments are not separate rows, and a
    soft-deleted row is excluded by the default manager.
    """
    start, end = range_bounds(baby.household, key, now=now)
    return (
        Event.objects.filter(
            baby=baby,
            household=baby.household,
            occurred_at__gte=start,
            occurred_at__lte=end,
        )
        .select_related('created_by')
        .prefetch_related('segments')
        .order_by('-occurred_at', '-recorded_at')
    )


def _seconds(value) -> int:
    if value is None:
        return 0
    return int(value)


def range_summary(events) -> dict:
    """Counts for one already-filtered list of events. Each event once."""
    diapers = {choice: 0 for choice, _label in Event.DiaperType.choices}
    diaper_total = 0
    feed_total = 0
    left = 0
    right = 0
    others = {kind: 0 for kind in OTHER_KIND_LABELS}
    seen = set()
    for event in events:
        if event.pk in seen:
            continue
        seen.add(event.pk)
        if event.kind == Event.Kind.DIAPER:
            diaper_total += 1
            if event.diaper_type in diapers:
                diapers[event.diaper_type] += 1
            continue
        if event.kind == Event.Kind.FEED:
            feed_total += 1
            event_left, event_right = display_totals(event)
            left += _seconds(event_left)
            right += _seconds(event_right)
            continue
        if event.kind in others:
            others[event.kind] += 1
    return {
        'diapers': {'total': diaper_total, **diapers},
        'feeds': {
            'total': feed_total,
            'left': left,
            'right': right,
            'duration': left + right,
        },
        'other': [
            {'kind': kind, 'label': label, 'count': others[kind]}
            for kind, label in OTHER_KIND_LABELS.items()
        ],
    }


def latest_diaper(baby):
    return (
        Event.objects.filter(baby=baby, kind=Event.Kind.DIAPER)
        .order_by('-occurred_at')
        .first()
    )


def latest_feed(baby):
    return (
        Event.objects.filter(baby=baby, kind=Event.Kind.FEED)
        .order_by('-occurred_at')
        .first()
    )


def last_side_used(baby) -> str:
    active = open_feed(baby)
    if active is not None:
        return current_side(active)
    finished = (
        Event.objects.filter(
            baby=baby,
            kind=Event.Kind.FEED,
            feed_state=Event.FeedState.FINISHED,
        )
        .order_by('-occurred_at')
        .first()
    )
    if finished is None:
        return ''
    return finished.end_side or finished.start_side or ''


def time_since(instant: datetime | None, *, now=None):
    if instant is None:
        return None
    now = now or timezone.now()
    return now - instant


def change_marker(baby) -> str:
    """Cheap ETag material: latest write and how many visible rows."""
    rows = Event.objects.filter(baby=baby, kind__in=VISIBLE_KINDS)
    latest = rows.order_by('-updated_at').values_list('updated_at', 'version').first()
    count = rows.count()
    if latest is None:
        return f'0:{count}'
    updated_at, version = latest
    return f'{updated_at.isoformat()}:{version}:{count}'
