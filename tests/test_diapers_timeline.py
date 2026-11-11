"""Diaper writes, conflicts, and local-day timeline grouping."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from django.utils import timezone as dj_timezone

from tests.helpers import add_member, login, make_household
from tracker.diapers import create_diaper
from tracker.extra_models import Role
from tracker.models import Event
from tracker.summary import events_on_day, group_local_days

pytestmark = pytest.mark.django_db
UTC = timezone.utc


def test_diaper_validation_idempotency_and_audit(caplog):
    household, baby, actor = make_household()
    note = 'NoteTokenZz91'
    when = datetime(2026, 6, 15, 15, tzinfo=UTC)
    event, replay = create_diaper(
        actor=actor,
        baby=baby,
        diaper_type='both',
        occurred_at=when,
        notes=note,
        idempotency_key='diaper-key-1',
    )
    again, replay_again = create_diaper(
        actor=actor,
        baby=baby,
        diaper_type='wet',
        occurred_at=when,
        notes='other',
        idempotency_key='diaper-key-1',
    )
    assert replay is False
    assert replay_again is True
    assert again.pk == event.pk
    assert Event.objects.filter(kind=Event.Kind.DIAPER).count() == 1
    assert event.diaper_type == 'both'
    assert event.recorded_at.tzinfo is not None
    assert event.history().count() == 1
    assert note not in caplog.text

    with pytest.raises(Exception):
        create_diaper(
            actor=actor,
            baby=baby,
            diaper_type='nope',
            occurred_at=when,
            notes='',
            idempotency_key='bad-kind',
        )
    future = dj_timezone.now() + timedelta(hours=3)
    with pytest.raises(Exception):
        create_diaper(
            actor=actor,
            baby=baby,
            diaper_type='dry',
            occurred_at=future,
            notes='',
            idempotency_key='future-diaper',
        )
    assert Event.objects.count() == 1
    assert household.timezone == 'UTC'


def test_double_post_and_conflict_and_restore(client):
    household, baby, admin = make_household()
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_diaper_parent'
    )
    caregiver, _care = add_member(
        household, Role.CAREGIVER, actor=admin, username='synth_diaper_care'
    )
    login(client, parent)
    payload = {
        'diaper_type': 'wet',
        'idempotency_key': 'ui-key-1',
        'notes': 'first',
    }
    assert client.post('/diapers/', payload).status_code == 200
    assert client.post('/diapers/', payload).status_code == 200
    assert Event.objects.filter(kind=Event.Kind.DIAPER).count() == 1
    event = Event.objects.get()
    other = client.__class__()
    login(other, caregiver)
    own, _replay = create_diaper(
        actor=caregiver,
        baby=baby,
        diaper_type='dry',
        occurred_at=datetime(2026, 6, 15, 16, tzinfo=UTC),
        notes='mine',
        idempotency_key='care-own',
    )
    login(other, caregiver)
    saved = other.post(
        f'/events/{own.pk}/edit/',
        {
            'version': own.version,
            'diaper_type': 'dirty',
            'notes': 'edited',
            'occurred_at': '2026-06-15T16:05',
        },
    )
    assert saved.status_code == 302
    first = client.post(
        f'/events/{event.pk}/edit/',
        {'version': event.version, 'diaper_type': 'dirty', 'notes': 'updated'},
    )
    assert first.status_code == 302
    conflict = client.post(
        f'/events/{event.pk}/edit/',
        {'version': event.version, 'diaper_type': 'wet', 'notes': 'lost'},
    )
    assert conflict.status_code == 409
    assert b'changed' in conflict.content
    fresh = Event.objects.get(pk=event.pk)
    assert fresh.diaper_type == 'dirty'
    deleted = client.post(
        f'/events/{fresh.pk}/delete/',
        {'version': fresh.version},
    )
    assert deleted.status_code == 302
    assert not Event.objects.filter(pk=fresh.pk).exists()
    hidden = Event.all_objects.get(pk=fresh.pk)
    restored = client.post(f'/events/{hidden.pk}/restore/')
    assert restored.status_code == 302
    assert Event.objects.filter(pk=fresh.pk).exists()
    # Caregiver cannot restore.
    care_client = client.__class__()
    login(care_client, caregiver)
    care_event = Event.objects.get(pk=own.pk)
    care_client.post(
        f'/events/{care_event.pk}/delete/',
        {'version': care_event.version},
    )
    denied = care_client.post(f'/events/{care_event.pk}/restore/')
    assert denied.status_code == 403


def test_local_days_dst_and_timezone_change():
    household, baby, actor = make_household(timezone='UTC')
    late, _replay = create_diaper(
        actor=actor,
        baby=baby,
        diaper_type='wet',
        occurred_at=datetime(2026, 6, 15, 23, 59, tzinfo=UTC),
        notes='',
        idempotency_key='late',
    )
    early, _replay = create_diaper(
        actor=actor,
        baby=baby,
        diaper_type='dry',
        occurred_at=datetime(2026, 6, 16, 0, 1, tzinfo=UTC),
        notes='',
        idempotency_key='early',
    )
    assert list(events_on_day(baby, late.occurred_at.date())) == [late]
    assert list(events_on_day(baby, early.occurred_at.date())) == [early]
    grouped = group_local_days(household, Event.objects.all())
    assert set(grouped) == {late.occurred_at.date(), early.occurred_at.date()}

    household.timezone = 'America/Los_Angeles'
    household.save(actor=actor, expected_version=household.version)
    baby.refresh_from_db()
    regrouped = group_local_days(household, Event.objects.all())
    # 23:59 UTC is 16:59 in Los Angeles, still the 15th.
    # 00:01 UTC on the 16th is 17:01 on the 15th in Los Angeles.
    assert set(regrouped) == {datetime(2026, 6, 15).date()}
    assert late.occurred_at == datetime(2026, 6, 15, 23, 59, tzinfo=UTC)

    pacific, pacific_baby, pacific_actor = make_household(timezone='America/New_York')
    spring_start, spring_end = pacific.local_day_bounds(datetime(2026, 3, 8).date())
    fall_start, fall_end = pacific.local_day_bounds(datetime(2026, 11, 1).date())
    assert spring_end - spring_start == timedelta(hours=23)
    assert fall_end - fall_start == timedelta(hours=25)
    create_diaper(
        actor=pacific_actor,
        baby=pacific_baby,
        diaper_type='wet',
        occurred_at=spring_start,
        notes='',
        idempotency_key='spring-start',
    )
    create_diaper(
        actor=pacific_actor,
        baby=pacific_baby,
        diaper_type='dry',
        occurred_at=spring_end - timedelta(seconds=1),
        notes='',
        idempotency_key='spring-end',
    )
    create_diaper(
        actor=pacific_actor,
        baby=pacific_baby,
        diaper_type='wet',
        occurred_at=spring_end,
        notes='',
        idempotency_key='spring-next',
    )
    spring_day = list(events_on_day(pacific_baby, datetime(2026, 3, 8).date()))
    assert len(spring_day) == 2
    create_diaper(
        actor=pacific_actor,
        baby=pacific_baby,
        diaper_type='both',
        occurred_at=fall_start,
        notes='',
        idempotency_key='fall-start',
    )
    create_diaper(
        actor=pacific_actor,
        baby=pacific_baby,
        diaper_type='dirty',
        occurred_at=fall_end - timedelta(seconds=1),
        notes='',
        idempotency_key='fall-end',
    )
    fall_day = list(events_on_day(pacific_baby, datetime(2026, 11, 1).date()))
    assert len(fall_day) == 2
    deleted = fall_day[0]
    deleted.delete(actor=pacific_actor, expected_version=deleted.version)
    assert deleted.pk not in {
        row.pk for row in events_on_day(pacific_baby, datetime(2026, 11, 1).date())
    }
