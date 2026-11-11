"""Feeding totals, manual entry, and the one-active-feed constraint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone as dj_timezone

from tests.helpers import make_household
from tracker.exceptions import VersionConflict
from tracker.extra_models import FeedingSegment
from tracker.feeds import (
    apply_timer,
    create_manual_feed,
    display_totals,
    open_feed,
    update_feed_totals,
)
from tracker.models import Event
from tracker.summary import day_feed_totals, events_on_day, last_side_used

pytestmark = pytest.mark.django_db
UTC = timezone.utc


def _start(actor, baby, when, side='left', key='start-1'):
    return apply_timer(
        actor=actor,
        baby=baby,
        action='start',
        side=side,
        idempotency_key=key,
        expected_version=None,
        now=when,
    )


def test_database_rejects_a_second_open_feed_and_a_second_open_segment():
    _household, baby, actor = make_household()
    when = datetime(2026, 6, 15, 12, tzinfo=UTC)
    first, _replay = _start(actor, baby, when)
    second, replay = _start(
        actor, baby, when + timedelta(minutes=5), side='right', key='start-2'
    )
    assert second.pk == first.pk
    assert replay is False
    assert (
        Event.objects.filter(
            baby=baby,
            feed_state__in=['active', 'paused'],
        ).count()
        == 1
    )
    duplicate = Event(
        baby=baby,
        kind=Event.Kind.FEED,
        feed_state=Event.FeedState.ACTIVE,
        feed_source=Event.FeedSource.TIMER,
        occurred_at=when,
        recorded_at=when,
    )
    with pytest.raises(ValidationError):
        duplicate.save(actor=actor)
    with pytest.raises(Exception):
        FeedingSegment.objects.create(
            session=first,
            side='right',
            started_at=when,
        )


def test_switch_is_contiguous_and_retry_is_a_no_op():
    _household, baby, actor = make_household()
    start = datetime(2026, 6, 15, 12, tzinfo=UTC)
    session, _replay = _start(actor, baby, start, key='seq-start')
    switched, _replay = apply_timer(
        actor=actor,
        baby=baby,
        action='switch',
        side='',
        idempotency_key='seq-switch',
        expected_version=session.version,
        now=start + timedelta(seconds=30),
    )
    segments = list(switched.segments.order_by('started_at'))
    assert len(segments) == 2
    assert segments[0].ended_at == segments[1].started_at
    assert segments[0].side == 'left'
    assert segments[1].side == 'right'
    again, replay = apply_timer(
        actor=actor,
        baby=baby,
        action='pause',
        side='',
        idempotency_key='seq-switch',
        expected_version=switched.version,
        now=start + timedelta(seconds=40),
    )
    assert replay is True
    assert again.segments.filter(ended_at__isnull=True).count() == 1
    with pytest.raises(VersionConflict):
        apply_timer(
            actor=actor,
            baby=baby,
            action='pause',
            side='',
            idempotency_key='seq-stale',
            expected_version=1,
            now=start + timedelta(seconds=45),
        )
    assert open_feed(baby).feed_state == 'active'
    finished, _replay = apply_timer(
        actor=actor,
        baby=baby,
        action='finish',
        side='',
        idempotency_key='seq-finish',
        expected_version=switched.version,
        now=start + timedelta(seconds=50),
    )
    left, right = display_totals(finished)
    assert left == 30
    assert right == 20
    assert finished.feed_state == 'finished'
    assert (
        FeedingSegment.objects.filter(session=finished, ended_at__isnull=True).count()
        == 0
    )


def test_two_devices_share_one_sequence():
    _household, baby, actor_a = make_household()
    from tests.helpers import make_user

    actor_b = make_user('synth_device_b')
    start = datetime(2026, 6, 15, 8, tzinfo=UTC)
    session, _replay = _start(actor_a, baby, start, side='left', key='dev-a-start')
    switched, _replay = apply_timer(
        actor=actor_b,
        baby=baby,
        action='switch',
        side='',
        idempotency_key='dev-b-switch',
        expected_version=session.version,
        now=start + timedelta(seconds=10),
    )
    finished, _replay = apply_timer(
        actor=actor_a,
        baby=baby,
        action='finish',
        side='',
        idempotency_key='dev-a-finish',
        expected_version=switched.version,
        now=start + timedelta(seconds=25),
    )
    left, right = display_totals(finished)
    assert (left, right) == (10, 15)


def test_manual_zero_is_not_unknown_and_edit_is_audited():
    _household, baby, actor = make_household()
    when = datetime(2026, 6, 15, 9, tzinfo=UTC)
    event, replay = create_manual_feed(
        actor=actor,
        baby=baby,
        occurred_at=when,
        left_seconds=120,
        right_seconds=0,
        start_side='left',
        end_side='left',
        notes='',
        idempotency_key='manual-1',
    )
    again, replay_again = create_manual_feed(
        actor=actor,
        baby=baby,
        occurred_at=when,
        left_seconds=10,
        right_seconds=10,
        start_side='',
        end_side='',
        notes='',
        idempotency_key='manual-1',
    )
    assert replay is False and replay_again is True
    assert again.pk == event.pk
    assert event.left_seconds == 120
    assert event.right_seconds == 0
    assert event.right_seconds is not None
    assert event.segments.count() == 0
    with pytest.raises(ValidationError):
        create_manual_feed(
            actor=actor,
            baby=baby,
            occurred_at=when,
            left_seconds=None,
            right_seconds=30,
            start_side='',
            end_side='',
            notes='',
            idempotency_key='manual-blank',
        )
    updated = update_feed_totals(
        actor=actor,
        event=event,
        expected_version=event.version,
        left_seconds=90,
        right_seconds=30,
        notes='corrected',
    )
    assert updated.totals_overridden is True
    assert updated.history().filter(action='update').exists()
    assert last_side_used(baby) == 'left'


def test_midnight_feed_counts_on_the_start_day():
    household, baby, actor = make_household(timezone='UTC')
    create_manual_feed(
        actor=actor,
        baby=baby,
        occurred_at=datetime(2026, 6, 15, 23, 30, tzinfo=UTC),
        left_seconds=50 * 60,
        right_seconds=0,
        start_side='left',
        end_side='left',
        notes='',
        idempotency_key='midnight-feed',
    )
    today = list(events_on_day(baby, datetime(2026, 6, 15).date(), kind='feed'))
    tomorrow = list(events_on_day(baby, datetime(2026, 6, 16).date(), kind='feed'))
    assert len(today) == 1
    assert tomorrow == []
    left, _right, count = day_feed_totals(today)
    assert count == 1
    assert left == 50 * 60
    assert household.timezone == 'UTC'
    assert dj_timezone.now()
