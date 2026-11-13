"""Timer and manual feeds.

Timer totals come from segments. Manual and imported totals are stored on
the event. A missing duration is null; zero is zero. One non-finished feed
per baby is enforced by a partial unique index and a row lock on the baby.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from tracker.clock import ensure_aware, reject_if_too_far_future, require_key
from tracker.exceptions import DeletedRecord, VersionConflict
from tracker.extra_models import FeedingSegment, RequestKey
from tracker.models import BabyProfile, Event

logger = logging.getLogger(__name__)

OPEN_STATES = (Event.FeedState.ACTIVE, Event.FeedState.PAUSED)
TIMER_ACTIONS = {'start', 'pause', 'resume', 'switch', 'finish'}


def segment_seconds(session, *, now=None) -> tuple[int, int]:
    now = now or timezone.now()
    left = 0
    right = 0
    for segment in session.segments.all():
        end = segment.ended_at or now
        elapsed = int((end - segment.started_at).total_seconds())
        if elapsed < 0:
            elapsed = 0
        if segment.side == Event.Side.LEFT:
            left += elapsed
        elif segment.side == Event.Side.RIGHT:
            right += elapsed
    return left, right


def display_totals(session, *, now=None) -> tuple[int | None, int | None]:
    if session.feed_source == Event.FeedSource.TIMER and not session.totals_overridden:
        return segment_seconds(session, now=now)
    return session.left_seconds, session.right_seconds


def open_feed(baby):
    return (
        Event.objects.filter(
            baby=baby,
            kind=Event.Kind.FEED,
            feed_state__in=OPEN_STATES,
        )
        .order_by('occurred_at')
        .first()
    )


def current_side(session, *, now=None) -> str:
    del now
    open_segment = (
        session.segments.filter(ended_at__isnull=True).order_by('-started_at').first()
    )
    if open_segment is not None:
        return open_segment.side
    return session.end_side or session.start_side or ''


def _lock_baby(baby):
    return BabyProfile.all_objects.select_for_update().get(pk=baby.pk)


def _replay(actor, scope, key):
    return (
        RequestKey.objects.filter(actor=actor, scope=scope, key=key)
        .select_related('event')
        .first()
    )


def _remember(actor, scope, key, action, event):
    RequestKey.objects.create(
        actor=actor,
        scope=scope,
        key=key,
        action=action,
        event=event,
    )


def _close_open_segment(session, when):
    segment = (
        FeedingSegment.objects.select_for_update()
        .filter(session=session, ended_at__isnull=True)
        .first()
    )
    if segment is None:
        return None
    segment.ended_at = when
    segment.save(update_fields=['ended_at'])
    return segment


def _open_segment(session, side, when):
    FeedingSegment.objects.create(session=session, side=side, started_at=when)


def apply_timer(
    *,
    actor,
    baby,
    action: str,
    side: str,
    idempotency_key: str,
    expected_version: int | None,
    now=None,
):
    key = require_key(idempotency_key)
    if action not in TIMER_ACTIONS:
        raise ValidationError({'action': 'Unknown timer action.'})
    if action in {'start', 'resume', 'switch'} and side not in Event.Side.values:
        if action != 'switch':
            raise ValidationError({'side': 'Choose left or right.'})
    now = now or timezone.now()
    try:
        with transaction.atomic():
            _lock_baby(baby)
            prior = _replay(actor, 'feed', key)
            if prior is not None:
                return prior.event, True
            if action == 'start':
                return _start(
                    actor=actor,
                    baby=baby,
                    side=side,
                    key=key,
                    now=now,
                )
            session = open_feed(baby)
            if session is None:
                raise ValidationError('There is no active feed.')
            if expected_version is None or session.version != expected_version:
                fresh = session.version
                raise VersionConflict(
                    model_label=Event._meta.label,
                    pk=session.pk,
                    expected_version=expected_version or -1,
                    current_version=fresh,
                )
            if action == 'pause':
                _pause(session, actor, now)
            elif action == 'resume':
                _resume(session, actor, side, now)
            elif action == 'switch':
                _switch(session, actor, now)
            elif action == 'finish':
                _finish(session, actor, now)
            _remember(actor, 'feed', key, action, session)
            session.refresh_from_db()
            return session, False
    except IntegrityError:
        prior = _replay(actor, 'feed', key)
        if prior is not None:
            return prior.event, True
        raise


def _start(*, actor, baby, side, key, now):
    if side not in Event.Side.values:
        raise ValidationError({'side': 'Choose left or right.'})
    existing = open_feed(baby)
    if existing is not None:
        _remember(actor, 'feed', key, 'start', existing)
        return existing, False
    session = Event(
        baby=baby,
        kind=Event.Kind.FEED,
        feed_state=Event.FeedState.ACTIVE,
        feed_source=Event.FeedSource.TIMER,
        occurred_at=now,
        recorded_at=now,
        start_side=side,
        end_side=side,
        left_seconds=0,
        right_seconds=0,
    )
    session.save(actor=actor)
    _open_segment(session, side, now)
    _remember(actor, 'feed', key, 'start', session)
    return session, False


def _pause(session, actor, now):
    if session.feed_state != Event.FeedState.ACTIVE:
        raise ValidationError('The feed is not running.')
    _close_open_segment(session, now)
    session.feed_state = Event.FeedState.PAUSED
    session.save(actor=actor, expected_version=session.version)


def _resume(session, actor, side, now):
    if session.feed_state != Event.FeedState.PAUSED:
        raise ValidationError('The feed is not paused.')
    if side not in Event.Side.values:
        raise ValidationError({'side': 'Choose left or right.'})
    _open_segment(session, side, now)
    session.feed_state = Event.FeedState.ACTIVE
    session.end_side = side
    session.save(actor=actor, expected_version=session.version)


def _switch(session, actor, now):
    if session.feed_state != Event.FeedState.ACTIVE:
        raise ValidationError('The feed is not running.')
    closed = _close_open_segment(session, now)
    if closed is None:
        raise ValidationError('There is no open side to switch.')
    other = Event.Side.RIGHT if closed.side == Event.Side.LEFT else Event.Side.LEFT
    _open_segment(session, other, now)
    session.end_side = other
    session.feed_state = Event.FeedState.ACTIVE
    session.save(actor=actor, expected_version=session.version)


def _finish(session, actor, now):
    _close_open_segment(session, now)
    left, right = segment_seconds(session, now=now)
    session.feed_state = Event.FeedState.FINISHED
    session.feed_ended_at = now
    session.left_seconds = left
    session.right_seconds = right
    session.totals_overridden = False
    session.save(actor=actor, expected_version=session.version)


def create_manual_feed(
    *,
    actor,
    baby,
    occurred_at,
    left_seconds,
    right_seconds,
    start_side: str,
    end_side: str,
    notes: str,
    idempotency_key: str,
    now=None,
):
    """A manual feed stores 0 for an unused side. Null means unknown."""
    key = require_key(idempotency_key)
    occurred_at = ensure_aware(occurred_at, 'occurred_at')
    now = now or timezone.now()
    reject_if_too_far_future(occurred_at, now=now)
    left_seconds, right_seconds = _manual_durations(left_seconds, right_seconds)
    start_side = start_side or ''
    end_side = end_side or ''
    if start_side and start_side not in Event.Side.values:
        raise ValidationError({'start_side': 'Choose left or right.'})
    if end_side and end_side not in Event.Side.values:
        raise ValidationError({'end_side': 'Choose left or right.'})
    notes = notes or ''
    if len(notes) > 2000:
        raise ValidationError({'notes': 'Notes are limited to 2000 characters.'})
    prior = _replay(actor, 'manual-feed', key)
    if prior is not None:
        return prior.event, True
    try:
        with transaction.atomic():
            event = Event(
                baby=baby,
                kind=Event.Kind.FEED,
                feed_state=Event.FeedState.FINISHED,
                feed_source=Event.FeedSource.MANUAL,
                occurred_at=occurred_at,
                recorded_at=now,
                feed_ended_at=occurred_at
                + timedelta(seconds=left_seconds + right_seconds),
                left_seconds=left_seconds,
                right_seconds=right_seconds,
                start_side=start_side,
                end_side=end_side or start_side,
                notes=notes,
                totals_overridden=True,
            )
            event.save(actor=actor)
            _remember(actor, 'manual-feed', key, 'manual', event)
    except IntegrityError:
        prior = _replay(actor, 'manual-feed', key)
        if prior is not None:
            return prior.event, True
        raise
    return event, False


def _manual_durations(left, right):
    if left is None or right is None:
        raise ValidationError(
            'Enter 0 for a side that was not used. Leave neither side blank.'
        )
    if left < 0 or right < 0:
        raise ValidationError('Durations cannot be negative.')
    if left == 0 and right == 0:
        raise ValidationError('At least one side must be longer than zero.')
    return int(left), int(right)


def update_feed_totals(
    *,
    actor,
    event,
    expected_version: int,
    left_seconds,
    right_seconds,
    notes: str | None = None,
    occurred_at=None,
):
    if event.kind != Event.Kind.FEED:
        raise ValidationError('That entry is not a feed.')
    if event.deleted_at is not None:
        raise DeletedRecord(model_label=Event._meta.label, pk=event.pk)
    left_seconds, right_seconds = _manual_durations(left_seconds, right_seconds)
    event.left_seconds = left_seconds
    event.right_seconds = right_seconds
    event.totals_overridden = True
    if notes is not None:
        if len(notes) > 2000:
            raise ValidationError({'notes': 'Notes are limited to 2000 characters.'})
        event.notes = notes
    if occurred_at is not None:
        event.occurred_at = ensure_aware(occurred_at, 'occurred_at')
        reject_if_too_far_future(event.occurred_at)
    event.save(actor=actor, expected_version=expected_version)
    return event


def log_timer_failure() -> None:
    logger.warning('timer save failed')


def side_totals_for_day(events) -> tuple[int, int, int]:
    """Sum stored or derived totals. `events` is an iterable of feed events."""
    left = 0
    right = 0
    count = 0
    for event in events:
        event_left, event_right = display_totals(event)
        left += event_left or 0
        right += event_right or 0
        count += 1
    return left, right, count
