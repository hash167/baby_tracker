"""Activity summary math and ago labels. Synthetic times only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import add_member, login, make_household
from tracker.activity import ago_label, sheet
from tracker.extra_models import Role
from tracker.models import Event

pytestmark = pytest.mark.django_db
UTC = timezone.utc


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


def test_ago_label_hours_and_minutes():
    assert ago_label(timedelta(hours=1, minutes=45)) == '1h 45m ago'
    assert ago_label(timedelta(minutes=8)) == '8m ago'
    assert ago_label(timedelta(seconds=-5)) == '0m ago'
    assert ago_label(None) == '—'


def test_summary_today_versus_rolling_window_and_units():
    household, baby, actor = make_household(timezone='America/Los_Angeles')
    now = datetime(2026, 6, 15, 20, 0, tzinfo=UTC)
    _save(
        actor,
        baby,
        now - timedelta(hours=2),
        kind=Event.Kind.FEED,
        feed_state=Event.FeedState.FINISHED,
        feed_source=Event.FeedSource.MANUAL,
        left_seconds=600,
        right_seconds=300,
        totals_overridden=True,
    )
    _save(
        actor,
        baby,
        now - timedelta(hours=1),
        kind=Event.Kind.BOTTLE,
        archive={'bottle_type': 'formula', 'volume_ml': 90},
    )
    _save(
        actor,
        baby,
        now - timedelta(hours=30),
        kind=Event.Kind.DIAPER,
        diaper_type='wet',
    )
    _save(
        actor,
        baby,
        now - timedelta(hours=3),
        kind=Event.Kind.SLEEP,
        archive={
            'duration_seconds': 3600,
            'ended_at': (now - timedelta(hours=2)).isoformat(),
        },
    )
    other_household, other_baby, other_actor = make_household(timezone='UTC')
    _save(
        other_actor,
        other_baby,
        now - timedelta(hours=1),
        kind=Event.Kind.BOTTLE,
        archive={'bottle_type': 'breast_milk', 'volume_ml': 500},
    )
    today = sheet(baby, 'today', now=now)
    assert today['breast']['count'] == 1
    assert today['breast']['left'] == '10m'
    assert today['breast']['right'] == '5m'
    assert today['bottle']['count'] == 1
    assert today['bottle']['ml'] == '90 mL'
    assert 'fl oz' in today['bottle']['floz']
    assert today['bottle']['formula_ml'] == '90 mL'
    assert today['diaper']['count'] == 0
    rolling = sheet(baby, '48h', now=now)
    assert rolling['diaper']['wet'] == 1
    assert rolling['diaper']['count'] == 1
    assert rolling['bottle']['count'] == 1
    assert rolling['sleep']['count'] == 1
    assert other_household.pk != household.pk


def test_activity_page_and_summary_sheet(client):
    household, _baby, admin = make_household()
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_activity_parent'
    )
    login(client, parent)
    page = client.get('/')
    assert page.status_code == 200
    body = page.content.decode()
    assert 'id="activity"' in body
    assert 'Add a diaper' in body
    assert 'Synth Baby' in body or 'Baby' in body
    summary = client.get('/activity/summary/?range=24h')
    assert summary.status_code == 200
    text = summary.content.decode()
    assert 'Last 24 hours' in text
    assert 'Breastfeed' in text
    assert 'Bottle feed' in text
