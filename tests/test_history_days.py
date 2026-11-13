"""History lists name the household day, not only the clock time."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.helpers import add_member, login, make_household
from tracker.extra_models import Role
from tracker.models import AuditEntry, Event

pytestmark = pytest.mark.django_db


def _event(actor, baby, when, **fields):
    event = Event(
        household=baby.household,
        baby=baby,
        occurred_at=when,
        recorded_at=when,
        **fields,
    )
    event.save(actor=actor)
    return event


def test_range_renders_day_headers_and_midnight_span(client):
    household, baby, admin = make_household(timezone='UTC')
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_history_parent'
    )
    login(client, parent)
    now = timezone.now()
    _event(
        parent,
        baby,
        now - timedelta(hours=1),
        kind=Event.Kind.DIAPER,
        diaper_type='wet',
    )
    _event(
        parent,
        baby,
        now - timedelta(hours=26),
        kind=Event.Kind.DIAPER,
        diaper_type='dry',
    )
    _event(
        parent,
        baby,
        now - timedelta(days=3),
        kind=Event.Kind.DIAPER,
        diaper_type='dirty',
    )
    start = (now - timedelta(days=1)).replace(
        hour=23, minute=30, second=0, microsecond=0
    )
    end = now.replace(hour=0, minute=40, second=0, microsecond=0)
    if end <= start:
        end = start + timedelta(hours=1, minutes=10)
    _event(
        parent,
        baby,
        start,
        kind=Event.Kind.FEED,
        feed_state=Event.FeedState.FINISHED,
        feed_source=Event.FeedSource.MANUAL,
        feed_ended_at=end,
        left_seconds=600,
        right_seconds=0,
        totals_overridden=True,
    )
    page = client.get('/timeline/?range=7d')
    assert page.status_code == 200
    body = page.content.decode()
    assert 'Today,' in body
    assert 'Yesterday,' in body
    assert 'Today ' in body
    assert 'Yesterday ' in body
    assert ' – ' in body
    assert 'name="diaper_type"' not in body
    assert 'name="notes"' not in body
    assert '<h1>History</h1>' in body


def test_undo_soft_deletes_through_the_audit_path(client):
    household, _baby, admin = make_household()
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_undo_parent'
    )
    login(client, parent)
    saved = client.post(
        '/diapers/',
        {
            'diaper_type': 'wet',
            'idempotency_key': 'undo-key',
            'notes': 'keep off the toast',
        },
        HTTP_HX_REQUEST='true',
    )
    assert saved.status_code == 200
    text = saved.content.decode()
    assert 'Saved Wet diaper' in text
    assert 'Today ' in text
    assert 'keep off the toast' not in text
    event = Event.objects.get()
    undone = client.post(
        f'/events/{event.pk}/delete/',
        {'version': event.version},
        HTTP_HX_REQUEST='true',
    )
    assert undone.status_code == 200
    assert b'Removed' in undone.content
    event.refresh_from_db()
    assert event.deleted_at is not None
    assert event.history().filter(action=AuditEntry.Action.DELETE).exists()
    listed = client.get('/timeline/?range=24h')
    assert 'diaper-wet' not in listed.content.decode()
