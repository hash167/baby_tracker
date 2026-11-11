"""Local-day timeline totals.

A feed that crosses local midnight counts on the day it started.
Stored UTC instants do not change when the household timezone changes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from django.utils import timezone

from tracker.feeds import current_side, display_totals, open_feed
from tracker.models import Event
from tracker.policy import VISIBLE_KINDS


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
