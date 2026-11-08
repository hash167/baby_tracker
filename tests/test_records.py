"""Household, baby, event, soft delete, and audit behavior on PostgreSQL."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.utils import IntegrityError
from django.test.utils import CaptureQueriesContext

from tests.factories import CaregiverFactory
from tracker.exceptions import DeletedRecord, RecordNotDeleted, VersionConflict
from tracker.models import AuditEntry, BabyProfile, Event, Household

pytestmark = pytest.mark.django_db

UTC = timezone.utc
MARKER = 'synthetic-not-telemetry'


def _users():
    return (
        CaregiverFactory(),
        CaregiverFactory(),
        CaregiverFactory(),
        CaregiverFactory(),
    )


def _household(actor, **kwargs):
    household = Household(
        name=kwargs.pop('name', 'Synthetic household'),
        timezone=kwargs.pop('timezone', 'America/Los_Angeles'),
    )
    household.save(actor=actor)
    return household


def _baby(actor, household, name='Synthetic baby'):
    baby = BabyProfile(household=household, name=name)
    baby.save(actor=actor)
    return baby


def _event(actor, baby, occurred_at, **kwargs):
    event = Event(
        baby=baby,
        occurred_at=occurred_at,
        recorded_at=kwargs.pop('recorded_at', None),
        source_system=kwargs.pop('source_system', ''),
        source_activity_id=kwargs.pop('source_activity_id', ''),
        household_id=kwargs.pop('household_id', None),
    )
    event.save(actor=actor)
    return event


def test_household_rejects_unknown_timezone_and_stores_nothing():
    user = CaregiverFactory()
    for zone in ('PST', 'Not/AZone', 'America/Los_Angeles '):
        household = Household(name='Synthetic household', timezone=zone)
        with pytest.raises(ValidationError):
            household.save(actor=user)
    assert Household.all_objects.count() == 0


def test_household_can_have_several_babies_with_separate_history():
    user = CaregiverFactory()
    household = _household(user, timezone='UTC')
    first = _baby(user, household, name='Synthetic One')
    second = _baby(user, household, name='Synthetic Two')

    assert list(household.babies.order_by('name').values_list('name', flat=True)) == [
        'Synthetic One',
        'Synthetic Two',
    ]
    first_ids = set(first.history().values_list('pk', flat=True))
    second_ids = set(second.history().values_list('pk', flat=True))
    assert first_ids
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.parametrize(
    'kind',
    ['household', 'baby', 'event'],
)
def test_create_update_delete_and_restore_audit_each_actor(kind):
    creator, editor, deleter, restorer = _users()
    if kind == 'household':
        record = _household(creator, timezone='UTC')
        record.name = 'Synthetic household edited'
    elif kind == 'baby':
        record = _baby(creator, _household(creator, timezone='UTC'))
        record.name = 'Synthetic baby edited'
    else:
        baby = _baby(creator, _household(creator, timezone='UTC'), name=MARKER)
        record = _event(
            creator,
            baby,
            datetime(2026, 6, 15, 15, tzinfo=UTC),
        )
        record.occurred_at = datetime(2026, 6, 15, 16, tzinfo=UTC)

    record.save(actor=editor)
    record.delete(actor=deleter)
    model = type(record)
    assert not model.objects.filter(pk=record.pk).exists()
    assert model.all_objects.filter(pk=record.pk, deleted_at__isnull=False).exists()
    record.restore(actor=restorer)
    assert model.objects.filter(pk=record.pk).exists()

    history = list(record.history())
    assert [(entry.action, entry.actor_id) for entry in history] == [
        (AuditEntry.Action.CREATE, creator.pk),
        (AuditEntry.Action.UPDATE, editor.pk),
        (AuditEntry.Action.DELETE, deleter.pk),
        (AuditEntry.Action.RESTORE, restorer.pk),
    ]
    delete_entry = history[2]
    restore_entry = history[3]
    assert delete_entry.changes['deleted_at']['before'] is None
    assert delete_entry.changes['deleted_at']['after']
    assert restore_entry.changes['deleted_at']['before']
    assert restore_entry.changes['deleted_at']['after'] is None
    for entry in history:
        payload = json.dumps(entry.changes)
        assert creator.email not in payload
        assert creator.username not in payload
        assert MARKER not in str(entry)
        assert MARKER not in repr(entry)


def test_stale_version_raises_and_does_not_audit_or_overwrite():
    creator, editor, _deleter, _restorer = _users()
    baby = _baby(creator, _household(creator, timezone='UTC'), name=MARKER)
    event = _event(creator, baby, datetime(2026, 6, 15, 15, tzinfo=UTC))
    stale = Event.all_objects.get(pk=event.pk)
    event.occurred_at = datetime(2026, 6, 15, 16, tzinfo=UTC)
    event.save(actor=editor, expected_version=1)

    stale.occurred_at = datetime(2026, 6, 15, 17, tzinfo=UTC)
    with pytest.raises(VersionConflict) as caught:
        stale.save(actor=editor, expected_version=1)

    assert caught.value.expected_version == 1
    assert caught.value.current_version == 2
    assert MARKER not in str(caught.value)
    fresh = Event.objects.get(pk=event.pk)
    assert fresh.version == 2
    assert fresh.occurred_at == datetime(2026, 6, 15, 16, tzinfo=UTC)
    assert fresh.history().filter(action=AuditEntry.Action.UPDATE).count() == 1
    assert '2026-06-15T17:00:00' not in json.dumps(
        fresh.history().get(action=AuditEntry.Action.UPDATE).changes
    )


def test_save_without_changes_does_not_bump_version_or_audit():
    user = CaregiverFactory()
    household = _household(user, timezone='UTC')
    household.save(actor=user)
    household.refresh_from_db()
    assert household.version == 1
    assert household.history().count() == 1


def test_soft_deleted_baby_drops_out_of_default_relations_but_event_can_load_it():
    user = CaregiverFactory()
    household = _household(user, timezone='UTC')
    baby = _baby(user, household)
    event = _event(user, baby, datetime(2026, 6, 15, 15, tzinfo=UTC))
    baby.delete(actor=user)

    assert not household.babies.filter(pk=baby.pk).exists()
    assert not BabyProfile.objects.filter(pk=baby.pk).exists()
    assert BabyProfile.all_objects.filter(pk=baby.pk).exists()
    # Forward FKs use the unfiltered base manager, so the event keeps its baby.
    assert Event.objects.get(pk=event.pk).baby_id == baby.pk
    # Soft delete does not cascade.
    assert Event.objects.filter(pk=event.pk, deleted_at__isnull=True).exists()


def test_restore_of_active_row_and_update_of_deleted_row_fail():
    user = CaregiverFactory()
    household = _household(user, timezone='UTC')
    with pytest.raises(RecordNotDeleted):
        household.restore(actor=user)
    household.delete(actor=user)
    household.name = 'Synthetic other'
    with pytest.raises(DeletedRecord):
        household.save(actor=user)
    assert Household.all_objects.get(pk=household.pk).name == 'Synthetic household'


def test_recorded_at_is_server_set_and_frozen():
    user = CaregiverFactory()
    editor = CaregiverFactory()
    baby = _baby(user, _household(user, timezone='UTC'))
    occurred = datetime(2026, 6, 15, 15, tzinfo=UTC)
    event = Event(baby=baby, occurred_at=occurred)
    event.save(actor=user)
    assert event.recorded_at is not None
    assert event.recorded_at.utcoffset() == timedelta(0)
    recorded = event.recorded_at

    event.occurred_at = occurred + timedelta(minutes=30)
    event.recorded_at = recorded + timedelta(days=2)
    event.save(actor=editor)
    event.refresh_from_db()
    assert event.recorded_at == recorded
    assert event.occurred_at == occurred + timedelta(minutes=30)


def test_naive_occurred_at_is_rejected():
    user = CaregiverFactory()
    baby = _baby(user, _household(user, timezone='UTC'))
    event = Event(baby=baby, occurred_at=datetime(2026, 6, 15, 15, 0))
    with pytest.raises(ValidationError):
        event.save(actor=user)
    assert Event.all_objects.count() == 0


def test_direct_writes_that_skip_audit_are_rejected():
    user = CaregiverFactory()
    household = _household(user, timezone='UTC')
    with pytest.raises(TypeError):
        Household.objects.create(name='Other', timezone='UTC')
    with pytest.raises(RuntimeError):
        Household.objects.filter(pk=household.pk).update(name='Other')
    with pytest.raises(RuntimeError):
        Household.objects.filter(pk=household.pk).delete()
    with pytest.raises(RuntimeError):
        Household.objects.bulk_create(
            [Household(name='Other', timezone='UTC', created_by=user, updated_by=user)]
        )
    household.refresh_from_db()
    assert household.name == 'Synthetic household'
    assert household.version == 1


def test_failed_audit_rolls_back_the_update():
    user = CaregiverFactory()
    household = _household(user, timezone='UTC')

    def boom(*args, **kwargs):
        raise RuntimeError('audit write failed')

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(AuditEntry, 'record', boom)
    try:
        household.name = 'Should not stick'
        with pytest.raises(RuntimeError, match='audit write failed'):
            household.save(actor=user)
    finally:
        monkeypatch.undo()

    household.refresh_from_db()
    assert household.name == 'Synthetic household'
    assert household.version == 1
    assert household.history().count() == 1


def test_import_reference_is_optional_and_unique_per_household():
    user = CaregiverFactory()
    household = _household(user, timezone='UTC')
    other = _household(user, timezone='UTC', name='Other synthetic household')
    baby = _baby(user, household)
    other_baby = _baby(user, other)
    occurred = datetime(2026, 6, 15, 15, tzinfo=UTC)
    _event(user, baby, occurred)
    _event(user, baby, occurred + timedelta(minutes=1))
    first = _event(
        user,
        baby,
        occurred,
        source_system='synthetic-source',
        source_activity_id='activity-1',
    )
    second = Event(
        baby=other_baby,
        occurred_at=occurred,
        source_system='synthetic-source',
        source_activity_id='activity-1',
    )
    second.save(actor=user)

    duplicate = Event(
        baby=baby,
        occurred_at=occurred,
        source_system='synthetic-source',
        source_activity_id='activity-1',
    )
    with pytest.raises(ValidationError) as caught:
        duplicate.save(actor=user)
    assert 'activity-1' not in str(caught.value)
    assert Event.objects.filter(pk=first.pk).exists()
    assert Event.objects.filter(pk=second.pk).exists()

    missing_system = Event(
        baby=baby,
        occurred_at=occurred,
        source_system='',
        source_activity_id='activity-2',
    )
    with pytest.raises(ValidationError):
        missing_system.save(actor=user)


def test_soft_deleted_import_reference_stays_reserved():
    user = CaregiverFactory()
    baby = _baby(user, _household(user, timezone='UTC'))
    event = _event(
        user,
        baby,
        datetime(2026, 6, 15, 15, tzinfo=UTC),
        source_system='synthetic-source',
        source_activity_id='activity-kept',
    )
    event.delete(actor=user)
    duplicate = Event(
        baby=baby,
        occurred_at=datetime(2026, 6, 15, 16, tzinfo=UTC),
        source_system='synthetic-source',
        source_activity_id='activity-kept',
    )
    with pytest.raises(ValidationError):
        duplicate.save(actor=user)
    assert Event.all_objects.filter(source_activity_id='activity-kept').count() == 1


def test_database_rejects_a_duplicate_import_reference():
    user = CaregiverFactory()
    household = _household(user, timezone='UTC')
    baby = _baby(user, household)
    event = _event(
        user,
        baby,
        datetime(2026, 6, 15, 15, tzinfo=UTC),
        source_system='synthetic-source',
        source_activity_id='activity-db',
    )
    table = Event._meta.db_table
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {table} (
                        id, version, created_at, updated_at, deleted_at,
                        created_by_id, updated_by_id, household_id, baby_id,
                        occurred_at, recorded_at, source_system, source_activity_id
                    )
                    SELECT gen_random_uuid(), version, created_at, updated_at,
                           deleted_at, created_by_id, updated_by_id,
                           household_id, baby_id, occurred_at, recorded_at,
                           source_system, source_activity_id
                    FROM {table}
                    WHERE id = %s
                    """,
                    [event.pk],
                )
    assert Event.objects.filter(source_activity_id='activity-db').count() == 1


def test_version_check_constraint_rejects_zero():
    user = CaregiverFactory()
    household = _household(user, timezone='UTC')
    table = Household._meta.db_table
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f'UPDATE {table} SET version = 0 WHERE id = %s',
                    [household.pk],
                )
    assert Household.objects.get(pk=household.pk).version == 1


def test_update_sql_locks_the_row_and_predicates_on_version():
    user = CaregiverFactory()
    household = _household(user, timezone='UTC')
    household.name = 'Synthetic household renamed'
    with CaptureQueriesContext(connection) as captured:
        household.save(actor=user, expected_version=1)
    selects = [
        query['sql'] for query in captured.captured_queries if 'SELECT' in query['sql']
    ]
    updates = [
        query['sql']
        for query in captured.captured_queries
        if query['sql'].startswith('UPDATE')
    ]
    assert any('FOR UPDATE' in sql for sql in selects)
    assert len(updates) == 1
    assert updates[0].lower().count('version') >= 2


def test_audit_payload_is_queryable_in_the_database_and_not_logged(caplog):
    user = CaregiverFactory()
    caplog.set_level(logging.DEBUG)
    household = _household(user, timezone='UTC', name=MARKER)
    stored = household.history().get(action=AuditEntry.Action.CREATE)
    assert stored.changes['name']['before'] is None
    assert stored.changes['name']['after'] == MARKER

    table = AuditEntry._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT changes #>> '{{name,after}}',
                   jsonb_typeof(changes)
            FROM {table}
            WHERE object_id = %s AND action = 'create'
            """,
            [str(household.pk)],
        )
        after, kind = cursor.fetchone()
    assert after == MARKER
    assert kind == 'object'

    leaked = [
        record.getMessage()
        for record in caplog.records
        if MARKER in record.getMessage()
        and not record.name.startswith('django.db.backends')
    ]
    assert leaked == []


def test_events_on_uses_utc_storage_and_local_day_bounds():
    user = CaregiverFactory()
    editor = CaregiverFactory()
    household = _household(user, timezone='America/Los_Angeles')
    baby = _baby(user, household, name=MARKER)

    def add(instant):
        return _event(user, baby, instant)

    june_late = add(datetime(2026, 6, 15, 6, 59, tzinfo=UTC))
    june_early = add(datetime(2026, 6, 15, 7, 1, tzinfo=UTC))
    spring_inside = add(datetime(2026, 3, 8, 9, 30, tzinfo=UTC))
    spring_end = add(datetime(2026, 3, 9, 6, 59, tzinfo=UTC))
    fall_first_fold = add(datetime(2026, 11, 1, 8, 30, tzinfo=UTC))
    fall_second_fold = add(datetime(2026, 11, 1, 9, 30, tzinfo=UTC))
    fall_next_day = add(datetime(2026, 11, 2, 8, 1, tzinfo=UTC))
    deleted = add(datetime(2026, 6, 14, 18, tzinfo=UTC))
    deleted.delete(actor=user)

    def ids_on(day):
        return set(household.events_on(day).values_list('pk', flat=True))

    assert ids_on(date(2026, 6, 14)) == {june_late.pk}
    assert june_early.pk in ids_on(date(2026, 6, 15))
    assert deleted.pk not in ids_on(date(2026, 6, 14))
    assert ids_on(date(2026, 3, 8)) == {spring_inside.pk, spring_end.pk}
    assert ids_on(date(2026, 11, 1)) == {fall_first_fold.pk, fall_second_fold.pk}
    assert fall_next_day.pk in ids_on(date(2026, 11, 2))

    crossed = datetime(2026, 6, 15, 6, 30, tzinfo=UTC)
    crossed_event = add(crossed)
    assert household.local_date(crossed_event.occurred_at) == date(2026, 6, 14)
    household.timezone = 'America/New_York'
    household.save(actor=editor)
    crossed_event.refresh_from_db()
    assert crossed_event.occurred_at == crossed
    household.refresh_from_db()
    assert household.local_date(crossed_event.occurred_at) == date(2026, 6, 15)
