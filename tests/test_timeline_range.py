"""Rolling timeline ranges. Synthetic instants only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import add_member, login, make_household
from tracker.extra_models import FeedingSegment, Role
from tracker.models import Event
from tracker.summary import events_in_range, range_bounds, range_summary

pytestmark = pytest.mark.django_db
UTC = timezone.utc
NOW = datetime(2026, 6, 15, 18, 0, tzinfo=UTC)


def _save(actor, baby, **fields):
    when = fields.pop('occurred_at')
    event = Event(
        household=baby.household,
        baby=baby,
        occurred_at=when,
        recorded_at=when,
        **fields,
    )
    event.save(actor=actor)
    return event


def test_bounds_use_the_household_zone_and_a_rolling_duration():
    household, _baby, _actor = make_household(timezone='America/Los_Angeles')
    start, end = range_bounds(household, '24h', now=NOW)
    assert end == NOW
    assert start == NOW - timedelta(hours=24)
    week_start, week_end = range_bounds(household, '7d', now=NOW)
    assert week_end == NOW
    assert week_start == NOW - timedelta(days=7)


def test_range_counts_boundaries_imports_and_one_feed_once():
    _household, baby, actor = make_household(timezone='America/Los_Angeles')
    for diaper_type, shift in (
        ('wet', timedelta(hours=1)),
        ('dirty', timedelta(hours=23)),
        ('both', timedelta(hours=30)),
        ('dry', timedelta(days=6)),
    ):
        _save(
            actor,
            baby,
            kind=Event.Kind.DIAPER,
            diaper_type=diaper_type,
            occurred_at=NOW - shift,
            notes='',
        )
    _save(
        actor,
        baby,
        kind=Event.Kind.DIAPER,
        diaper_type='wet',
        occurred_at=NOW - timedelta(hours=24),
        source_system='nara',
        source_activity_id='act-range-wet',
        notes='imported wet',
    )
    _save(
        actor,
        baby,
        kind=Event.Kind.SLEEP,
        occurred_at=NOW - timedelta(hours=2),
        source_system='nara',
        source_activity_id='act-range-sleep',
        notes='',
    )
    feed = _save(
        actor,
        baby,
        kind=Event.Kind.FEED,
        feed_state=Event.FeedState.FINISHED,
        feed_source=Event.FeedSource.TIMER,
        occurred_at=NOW - timedelta(hours=3),
        notes='',
    )
    FeedingSegment.objects.create(
        session=feed,
        side=Event.Side.LEFT,
        started_at=feed.occurred_at,
        ended_at=feed.occurred_at + timedelta(seconds=100),
    )
    FeedingSegment.objects.create(
        session=feed,
        side=Event.Side.RIGHT,
        started_at=feed.occurred_at,
        ended_at=feed.occurred_at + timedelta(seconds=50),
    )
    _save(
        actor,
        baby,
        kind=Event.Kind.GROWTH,
        occurred_at=NOW - timedelta(days=3),
        notes='',
    )
    outside = _save(
        actor,
        baby,
        kind=Event.Kind.DIAPER,
        diaper_type='dirty',
        occurred_at=NOW - timedelta(hours=24, microseconds=1),
        notes='just outside 24h',
    )
    _save(
        actor,
        baby,
        kind=Event.Kind.MEDICAL,
        occurred_at=NOW - timedelta(days=7, seconds=1),
        notes='outside the week',
    )
    future = _save(
        actor,
        baby,
        kind=Event.Kind.DIAPER,
        diaper_type='dry',
        occurred_at=NOW + timedelta(seconds=1),
        notes='after now',
    )
    removed = _save(
        actor,
        baby,
        kind=Event.Kind.DIAPER,
        diaper_type='wet',
        occurred_at=NOW - timedelta(minutes=5),
        notes='deleted',
    )
    removed.delete(actor=actor, expected_version=removed.version)

    day = list(events_in_range(baby, '24h', now=NOW))
    summary = range_summary(day)
    assert summary['diapers'] == {
        'total': 3,
        'wet': 2,
        'dirty': 1,
        'both': 0,
        'dry': 0,
    }
    assert summary['feeds'] == {
        'total': 1,
        'left': 100,
        'right': 50,
        'duration': 150,
    }
    other = {item['kind']: item['count'] for item in summary['other']}
    assert other['sleep'] == 1
    assert other['growth'] == 0
    assert len(day) == len({event.pk for event in day})
    assert outside.pk not in {event.pk for event in day}
    assert future.pk not in {event.pk for event in day}
    assert removed.pk not in {event.pk for event in day}

    two_days = range_summary(events_in_range(baby, '48h', now=NOW))
    assert two_days['diapers']['total'] == 5
    assert two_days['diapers']['both'] == 1
    assert two_days['diapers']['dirty'] == 2
    assert outside.pk in {event.pk for event in events_in_range(baby, '48h', now=NOW)}

    week = range_summary(events_in_range(baby, '7d', now=NOW))
    assert week['diapers'] == {
        'total': 6,
        'wet': 2,
        'dirty': 2,
        'both': 1,
        'dry': 1,
    }
    week_other = {item['kind']: item['count'] for item in week['other']}
    assert week_other['growth'] == 1
    assert week_other['sleep'] == 1
    week_ids = {event.pk for event in events_in_range(baby, '7d', now=NOW)}
    assert future.pk not in week_ids
    edge = _save(
        actor,
        baby,
        kind=Event.Kind.UNKNOWN,
        occurred_at=NOW - timedelta(days=7),
        notes='',
    )
    week_edge = range_summary(events_in_range(baby, '7d', now=NOW))
    assert {item['kind']: item['count'] for item in week_edge['other']}['unknown'] == 1
    assert edge.pk in {event.pk for event in events_in_range(baby, '7d', now=NOW)}


def test_another_household_is_not_counted():
    _household, baby, actor = make_household(timezone='UTC')
    other_household, other_baby, other_actor = make_household(timezone='UTC')
    _save(
        actor,
        baby,
        kind=Event.Kind.DIAPER,
        diaper_type='wet',
        occurred_at=NOW - timedelta(hours=1),
        notes='home wet',
    )
    _save(
        other_actor,
        other_baby,
        kind=Event.Kind.DIAPER,
        diaper_type='dirty',
        occurred_at=NOW - timedelta(hours=1),
        notes='OtherHouseNoteQq',
    )
    rows = list(events_in_range(baby, '24h', now=NOW))
    summary = range_summary(rows)
    assert summary['diapers']['total'] == 1
    assert summary['diapers']['wet'] == 1
    assert summary['diapers']['dirty'] == 0
    assert all(event.household_id == baby.household_id for event in rows)
    assert other_household.pk != baby.household_id


def test_timeline_page_defaults_to_24h_and_partial_switches(client):
    household, baby, admin = make_household(timezone='UTC')
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_range_parent'
    )
    login(client, parent)
    _save(
        parent,
        baby,
        kind=Event.Kind.DIAPER,
        diaper_type='wet',
        occurred_at=datetime.now(UTC) - timedelta(hours=1),
        notes='range wet',
    )
    _save(
        parent,
        baby,
        kind=Event.Kind.DIAPER,
        diaper_type='dry',
        occurred_at=datetime.now(UTC) - timedelta(hours=30),
        notes='range dry',
    )
    page = client.get('/timeline/')
    assert page.status_code == 200
    body = page.content.decode()
    assert 'Last 24 hours' in body
    assert 'aria-current="page"' in body
    assert 'range wet' in body
    assert 'range dry' not in body
    assert 'id="diaper-total">1<' in body

    wider = client.get('/timeline/partial/?range=48h')
    assert wider.status_code == 200
    wider_body = wider.content.decode()
    assert 'Last 48 hours' in wider_body
    assert 'range wet' in wider_body
    assert 'range dry' in wider_body
    assert 'id="diaper-total">2<' in wider_body

    missing = client.get('/timeline/?range=nope')
    assert 'Last 24 hours' in missing.content.decode()
