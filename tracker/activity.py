"""Activity cards and the summary sheet.

Times use the household timezone. A feed with several segments counts once.
Volumes are stored as millilitres. This module does not log row contents.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from tracker.days import local_date, local_day_bounds
from tracker.feeds import display_totals
from tracker.models import Event
from tracker.nara import ML_PER_FLOZ
from tracker.summary import range_bounds

SUMMARY_RANGES = ('today', '24h', '48h', '7d')
SUMMARY_LABELS = {
    'today': 'Today',
    '24h': 'Last 24 hours',
    '48h': 'Last 48 hours',
    '7d': 'Last 7 days',
}


def summary_range(value: str | None) -> str:
    if value in SUMMARY_RANGES:
        return value
    return 'today'


def ago_label(delta) -> str:
    if delta is None:
        return '—'
    total = max(int(delta.total_seconds()), 0)
    minutes, _seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f'{days}d {hours}h ago'
    if hours:
        return f'{hours}h {minutes}m ago'
    return f'{minutes}m ago'


def _clock(local: datetime) -> str:
    hour = str(int(local.strftime('%I')))
    return f'{hour}:{local.strftime("%M")} {local.strftime("%p")}'


def _day_phrase(day, today) -> str:
    if day == today:
        return 'Today'
    if day == today - timedelta(days=1):
        return 'Yesterday'
    return f'{day.strftime("%a %b")} {day.day}'


def day_header(day, *, now=None) -> str:
    """Sticky heading: Today, Fri Nov 13 / Yesterday, Thu Nov 12 / Wed Nov 11."""
    from django.utils import timezone as dj_tz

    today = dj_tz.localtime(now or dj_tz.now()).date()
    dated = f'{day.strftime("%a %b")} {day.day}'
    phrase = _day_phrase(day, today)
    if phrase in ('Today', 'Yesterday'):
        return f'{phrase}, {dated}'
    return dated


def when_stamp(instant: datetime, *, now=None) -> str:
    """Today 10:31 AM, Yesterday 10:31 AM, or Fri Nov 13 · 10:31 AM."""
    from django.utils import timezone as dj_tz

    local = dj_tz.localtime(instant)
    today = dj_tz.localtime(now or dj_tz.now()).date()
    phrase = _day_phrase(local.date(), today)
    clock = _clock(local)
    if phrase in ('Today', 'Yesterday'):
        return f'{phrase} {clock}'
    return f'{phrase} · {clock}'


def when_span(start: datetime, end: datetime, *, now=None) -> str:
    """Full start and end when a feed crosses local midnight."""
    from django.utils import timezone as dj_tz

    start_local = dj_tz.localtime(start)
    end_local = dj_tz.localtime(end)
    if start_local.date() == end_local.date():
        return f'{when_stamp(start, now=now)}–{_clock(end_local)}'
    start_text = (
        f'{start_local.strftime("%a %b")} {start_local.day} · {_clock(start_local)}'
    )
    end_text = f'{end_local.strftime("%a %b")} {end_local.day} · {_clock(end_local)}'
    return f'{start_text} – {end_text}'


def clock_label(instant: datetime, zone: str) -> str:
    del zone
    return when_stamp(instant)


def duration_label(seconds) -> str:
    if seconds is None:
        return ''
    total = max(int(round(float(seconds))), 0)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours}h {minutes}m'
    if secs:
        return f'{minutes}m {secs}s'
    return f'{minutes}m'


def ml_label(value) -> str:
    if value is None:
        return ''
    number = float(value)
    if number.is_integer():
        shown = str(int(number))
    else:
        shown = f'{number:.1f}'
    return f'{shown} mL'


def floz_label(ml) -> str:
    if ml is None:
        return ''
    ounces = float(ml) / ML_PER_FLOZ
    if abs(ounces - round(ounces)) < 0.05:
        return f'{round(ounces):.0f} fl oz'
    return f'{ounces:.1f} fl oz'


def activity_board(baby, *, now=None) -> dict:
    now = now or timezone.now()
    zone = baby.household.timezone
    today = local_date(now, zone)
    start, end = local_day_bounds(today, zone)
    events = list(
        Event.objects.filter(
            baby=baby,
            household=baby.household,
            occurred_at__gte=start,
            occurred_at__lt=end,
        )
        .prefetch_related('segments')
        .order_by('-occurred_at', '-recorded_at')
    )
    feeds = [_feed_row(event, zone, now) for event in events if _is_feed_card(event)]
    pumps = [
        _pump_row(event, zone) for event in events if event.kind == Event.Kind.PUMP
    ]
    diapers = [
        _diaper_row(event, zone) for event in events if event.kind == Event.Kind.DIAPER
    ]
    sleeps = [
        _sleep_row(event, zone, today, now)
        for event in events
        if event.kind == Event.Kind.SLEEP
    ]
    _scale(feeds, 'breast')
    _scale(feeds, 'bottle')
    _scale_all(pumps)
    _scale_all(sleeps)
    return {
        'today': today,
        'zone': zone,
        'feed': _hero_feed(baby, now),
        'pump': _hero_pump(baby, now),
        'diaper': _hero_diaper(baby, now),
        'sleep': _hero_sleep(baby, now),
        'feeds': _split(feeds),
        'pumps': _split(pumps),
        'diapers': _split(diapers),
        'sleeps': _split(sleeps),
    }


def _scale(rows: list[dict], icon: str) -> None:
    subset = [row for row in rows if row.get('icon') == icon]
    _scale_all(subset)


def _scale_all(rows: list[dict]) -> None:
    peak = max((float(row.get('bar') or 0) for row in rows), default=0) or 1
    for row in rows:
        row['bar_pct'] = round(100 * float(row.get('bar') or 0) / peak)


def sheet(baby, key: str, *, now=None) -> dict:
    now = now or timezone.now()
    key = summary_range(key)
    events = list(_sheet_events(baby, key, now=now))
    breast = [event for event in events if event.kind == Event.Kind.FEED]
    bottles = [event for event in events if event.kind == Event.Kind.BOTTLE]
    pumps = [event for event in events if event.kind == Event.Kind.PUMP]
    diapers = [event for event in events if event.kind == Event.Kind.DIAPER]
    sleeps = [event for event in events if event.kind == Event.Kind.SLEEP]
    left = 0
    right = 0
    for event in breast:
        event_left, event_right = display_totals(event)
        left += _seconds(event_left)
        right += _seconds(event_right)
    bottle_ml = 0.0
    breast_ml = 0.0
    formula_ml = 0.0
    for event in bottles:
        amount = _event_ml(event)
        bottle_ml += amount
        kind = (event.archive or {}).get('bottle_type') or ''
        if kind == 'formula':
            formula_ml += amount
        elif kind == 'mixed':
            breast_ml += amount / 2
            formula_ml += amount / 2
        else:
            breast_ml += amount
    pump_ml = sum(_event_ml(event) for event in pumps)
    diaper_counts = {name: 0 for name in ('wet', 'dirty', 'both', 'dry')}
    for event in diapers:
        if event.diaper_type in diaper_counts:
            diaper_counts[event.diaper_type] += 1
    sleep_hours = sum(_sleep_hours(event) for event in sleeps)
    return {
        'key': key,
        'label': SUMMARY_LABELS[key],
        'breast': {
            'count': len(breast),
            'total': duration_label(left + right),
            'left': duration_label(left),
            'right': duration_label(right),
        },
        'bottle': {
            'count': len(bottles),
            'ml': ml_label(bottle_ml),
            'floz': floz_label(bottle_ml),
            'breast_ml': ml_label(breast_ml),
            'formula_ml': ml_label(formula_ml),
        },
        'pump': {'count': len(pumps), 'ml': ml_label(pump_ml)},
        'diaper': {'count': len(diapers), **diaper_counts},
        'sleep': {
            'count': len(sleeps),
            'hours': f'{sleep_hours:.1f} h',
        },
    }


def _sheet_events(baby, key: str, *, now):
    if key == 'today':
        day = local_date(now, baby.household.timezone)
        start, end = local_day_bounds(day, baby.household.timezone)
        window = dict(occurred_at__gte=start, occurred_at__lt=end)
    else:
        start, end = range_bounds(baby.household, key, now=now)
        window = dict(occurred_at__gte=start, occurred_at__lte=end)
    return (
        Event.objects.filter(baby=baby, household=baby.household, **window)
        .prefetch_related('segments')
        .order_by('-occurred_at')
    )


def _split(rows: list[dict]) -> dict:
    return {'preview': rows[:3], 'rest': rows[3:], 'all': rows}


def _is_feed_card(event) -> bool:
    return event.kind in (Event.Kind.FEED, Event.Kind.BOTTLE)


def _hero_feed(baby, now):
    event = (
        Event.objects.filter(
            baby=baby,
            household=baby.household,
            kind__in=(Event.Kind.FEED, Event.Kind.BOTTLE),
        )
        .prefetch_related('segments')
        .order_by('-occurred_at')
        .first()
    )
    if event is None:
        return {'ago': '—', 'amount': '—', 'kind': '', 'when': ''}
    amount = _feed_amount(event)
    return {
        'ago': ago_label(now - event.occurred_at),
        'when': when_stamp(event.occurred_at, now=now),
        'amount': amount or '—',
        'kind': event.kind,
    }


def _hero_pump(baby, now):
    event = (
        Event.objects.filter(baby=baby, household=baby.household, kind=Event.Kind.PUMP)
        .order_by('-occurred_at')
        .first()
    )
    if event is None:
        return {'ago': '—', 'amount': '—', 'when': ''}
    return {
        'ago': ago_label(now - event.occurred_at),
        'when': when_stamp(event.occurred_at, now=now),
        'amount': ml_label(_event_ml(event)) or '—',
    }


def _hero_diaper(baby, now):
    event = (
        Event.objects.filter(
            baby=baby, household=baby.household, kind=Event.Kind.DIAPER
        )
        .order_by('-occurred_at')
        .first()
    )
    if event is None:
        return {'ago': '—', 'label': '—', 'when': ''}
    return {
        'ago': ago_label(now - event.occurred_at),
        'when': when_stamp(event.occurred_at, now=now),
        'label': _diaper_word(event.diaper_type).lower() or '—',
        'type': event.diaper_type,
    }


def _hero_sleep(baby, now):
    event = (
        Event.objects.filter(baby=baby, household=baby.household, kind=Event.Kind.SLEEP)
        .order_by('-occurred_at')
        .first()
    )
    if event is None:
        return {'text': 'No sleep logged', 'active': False}
    if _sleep_open(event):
        return {
            'text': (
                f'Sleeping since {when_stamp(event.occurred_at, now=now)} '
                f'for {duration_label((now - event.occurred_at).total_seconds())}'
            ),
            'active': True,
        }
    ended = _sleep_end(event)
    if ended is None:
        return {'text': 'No sleep logged', 'active': False}
    return {
        'text': f'Woke up {when_stamp(ended, now=now)} · {ago_label(now - ended)}',
        'active': False,
    }


def _feed_row(event, zone: str, now) -> dict:
    del now
    if event.kind == Event.Kind.BOTTLE:
        amount = _event_ml(event)
        bottle_type = (event.archive or {}).get('bottle_type') or ''
        label = {
            'breast_milk': 'Breast Milk',
            'formula': 'Formula',
            'mixed': 'Breast Milk',
        }.get(bottle_type, 'Bottle')
        return {
            'pk': event.pk,
            'clock': clock_label(event.occurred_at, zone),
            'label': label,
            'value': ml_label(amount),
            'bar': amount,
            'icon': 'bottle',
            'kind': 'bottle',
        }
    left, right = display_totals(event)
    seconds = _seconds(left) + _seconds(right)
    return {
        'pk': event.pk,
        'clock': clock_label(event.occurred_at, zone),
        'label': 'Breastfeed',
        'value': duration_label(seconds),
        'bar': seconds,
        'icon': 'breast',
        'kind': 'feed',
    }


def _pump_row(event, zone: str) -> dict:
    amount = _event_ml(event)
    return {
        'pk': event.pk,
        'clock': clock_label(event.occurred_at, zone),
        'value': ml_label(amount),
        'bar': amount,
    }


def _diaper_row(event, zone: str) -> dict:
    archive = event.archive or {}
    bits = []
    texture = (archive.get('dirty_texture') or '').strip()
    color = (archive.get('dirty_color') or '').strip()
    detail = (archive.get('detail') or '').strip()
    if texture:
        bits.append(texture.title())
    if color:
        bits.append(color.title())
    if detail:
        bits.append(detail)
    return {
        'pk': event.pk,
        'clock': clock_label(event.occurred_at, zone),
        'type': event.diaper_type,
        'label': _diaper_word(event.diaper_type),
        'detail': ' • '.join(bits),
        'note': event.notes or '',
    }


def _sleep_row(event, zone: str, today, now) -> dict:
    start = event.occurred_at.astimezone(ZoneInfo(zone))
    ended = _sleep_end(event)
    if _sleep_open(event):
        span = f'{when_stamp(event.occurred_at, now=now)}–now'
        seconds = (now - event.occurred_at).total_seconds()
    elif ended is not None:
        span = when_span(event.occurred_at, ended, now=now)
        seconds = (ended - event.occurred_at).total_seconds()
    else:
        span = when_stamp(event.occurred_at, now=now)
        seconds = 0
    return {
        'pk': event.pk,
        'span': span,
        'value': duration_label(seconds),
        'bar': max(seconds, 0),
        'yesterday': start.date() < today,
        'note': event.notes or '',
    }


def _diaper_word(value: str) -> str:
    return {
        'wet': 'Wet',
        'dirty': 'Dirty',
        'both': 'Mix',
        'dry': 'Dry',
    }.get(value or '', value or '')


def _feed_amount(event) -> str:
    if event.kind == Event.Kind.BOTTLE:
        return ml_label(_event_ml(event))
    left, right = display_totals(event)
    return duration_label(_seconds(left) + _seconds(right))


def _seconds(value) -> int:
    if value is None:
        return 0
    return max(int(value), 0)


def _event_ml(event) -> float:
    from tracker.trends import _volume_ml

    return _volume_ml(event.archive)


def _sleep_open(event) -> bool:
    archive = event.archive or {}
    if archive.get('ended_at') or archive.get('duration_seconds') not in (None, ''):
        return False
    return True


def _sleep_end(event):
    archive = event.archive or {}
    raw = archive.get('ended_at') or ''
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _sleep_hours(event) -> float:
    from tracker.trends import _sleep_hours as hours

    return hours(event)
