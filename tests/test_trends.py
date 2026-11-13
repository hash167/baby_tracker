"""Household-local trend buckets. Synthetic instants only."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from tests.helpers import add_member, login, make_household
from tracker.days import local_day_bounds
from tracker.extra_models import FeedingSegment, Role
from tracker.models import Event
from tracker.nara import ML_PER_FLOZ
from tracker.trends import build_trends, previous_dates, trend_dates

pytestmark = pytest.mark.django_db
UTC = timezone.utc
NOW = datetime(2026, 3, 10, 16, 0, tzinfo=UTC)


def _save(actor, baby, when, **fields):
    event = Event(
        household=baby.household,
        baby=baby,
        occurred_at=when,
        recorded_at=when,
        **fields,
    )
    event.save(actor=actor)
    return event


def _inside(day: date, zone: str, *, seconds: int = 3600):
    start, end = local_day_bounds(day, zone)
    return start + timedelta(seconds=seconds)


def test_dates_and_previous_period_do_not_overlap():
    household, _baby, _actor = make_household(timezone='America/New_York')
    current = trend_dates(household, 7, now=NOW)
    previous = previous_dates(current)
    assert current[0] == date(2026, 3, 4)
    assert current[-1] == date(2026, 3, 10)
    assert len(current) == 7
    assert len(previous) == 7
    assert previous[-1] == current[0] - timedelta(days=1)
    assert set(previous).isdisjoint(current)


def test_dst_bucket_previous_average_gap_and_conversion():
    household, baby, actor = make_household(timezone='America/New_York')
    spring_start, spring_end = local_day_bounds(date(2026, 3, 8), 'America/New_York')
    assert spring_end - spring_start == timedelta(hours=23)
    _save(
        actor,
        baby,
        spring_end - timedelta(seconds=1),
        kind=Event.Kind.DIAPER,
        diaper_type='wet',
    )
    _save(
        actor,
        baby,
        spring_end,
        kind=Event.Kind.DIAPER,
        diaper_type='dry',
    )
    feed_at = _inside(date(2026, 3, 9), 'America/New_York')
    feed = _save(
        actor,
        baby,
        feed_at,
        kind=Event.Kind.FEED,
        feed_state=Event.FeedState.FINISHED,
        feed_source=Event.FeedSource.TIMER,
    )
    FeedingSegment.objects.create(
        session=feed,
        side=Event.Side.LEFT,
        started_at=feed_at,
        ended_at=feed_at + timedelta(seconds=600),
    )
    FeedingSegment.objects.create(
        session=feed,
        side=Event.Side.RIGHT,
        started_at=feed_at + timedelta(seconds=600),
        ended_at=feed_at + timedelta(seconds=900),
    )
    _save(
        actor,
        baby,
        feed_at + timedelta(hours=3),
        kind=Event.Kind.BOTTLE,
        archive={
            'volume_ml': None,
            'volume': {'amount': 2, 'unit': 'floz', 'ml': None},
        },
    )
    _save(
        actor,
        baby,
        feed_at + timedelta(hours=2),
        kind=Event.Kind.BOTTLE,
        archive={
            'volume_ml': 90,
            'volume': {'amount': 90, 'unit': 'ml', 'ml': 90},
        },
    )
    _save(
        actor,
        baby,
        _inside(date(2026, 3, 6), 'America/New_York'),
        kind=Event.Kind.SLEEP,
        archive={'duration_seconds': '7200', 'ended_at': ''},
    )
    _save(
        actor,
        baby,
        _inside(date(2026, 3, 5), 'America/New_York'),
        kind=Event.Kind.PUMP,
        archive={'volume_ml': 120, 'total': {'amount': 120, 'unit': 'ML', 'ml': 120}},
    )
    _save(
        actor,
        baby,
        _inside(date(2026, 2, 28), 'America/New_York'),
        kind=Event.Kind.DIAPER,
        diaper_type='dirty',
    )
    other_household, other_baby, other_actor = make_household(
        timezone='America/New_York'
    )
    _save(
        other_actor,
        other_baby,
        spring_end - timedelta(seconds=1),
        kind=Event.Kind.DIAPER,
        diaper_type='wet',
    )

    report = build_trends(baby, 7, now=NOW)
    by_label = {row['date']: row for row in report['rows']}
    assert len(report['rows']) == 7
    assert by_label[date(2026, 3, 4)]['wet'] == 0
    assert by_label[date(2026, 3, 8)]['wet'] == 1
    assert by_label[date(2026, 3, 8)]['dry'] == 0
    assert by_label[date(2026, 3, 9)]['dry'] == 1
    assert by_label[date(2026, 3, 9)]['feeds'] == 1
    assert by_label[date(2026, 3, 9)]['left_minutes'] == 10
    assert by_label[date(2026, 3, 9)]['right_minutes'] == 5
    assert by_label[date(2026, 3, 9)]['bottles'] == 2
    assert by_label[date(2026, 3, 9)]['bottle_ml'] == pytest.approx(
        90 + 2 * ML_PER_FLOZ
    )
    assert by_label[date(2026, 3, 6)]['sleep_hours'] == 2
    assert by_label[date(2026, 3, 5)]['pump_ml'] == 120
    assert report['headlines']['diapers_per_day']['previous'] == pytest.approx(1 / 7)
    assert report['headlines']['diapers_per_day']['current'] == pytest.approx(2 / 7)
    assert report['feed_gap_seconds'] == pytest.approx(1.5 * 3600)
    assert other_household.pk != household.pk
    assert all(row['wet'] != 2 for row in report['rows'])


def test_trends_page_switches_range(client):
    household, _baby, admin = make_household()
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_trends_parent'
    )
    login(client, parent)
    page = client.get('/trends/')
    assert page.status_code == 200
    body = page.content.decode()
    assert 'Last 7 days' in body
    assert 'Trends' in body
    assert '<table' in body
    assert body.count('<th scope="row">') == 7 * 8
    wider = client.get('/trends/partial/?days=30')
    assert 'Last 30 days' in wider.content.decode()
    assert wider.content.decode().count('<th scope="row">') == 30 * 8
    fallback = client.get('/trends/?days=9')
    assert 'Last 7 days' in fallback.content.decode()
