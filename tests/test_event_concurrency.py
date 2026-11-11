"""Optimistic version conflicts under real PostgreSQL transactions."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from django.db import connection, connections, transaction
from django.db.utils import OperationalError

from tests.factories import CaregiverFactory
from tracker.exceptions import VersionConflict
from tracker.models import AuditEntry, BabyProfile, Event, Household

pytestmark = pytest.mark.django_db(transaction=True)

UTC = timezone.utc


def _event():
    actor = CaregiverFactory()
    household = Household(name='Synthetic household', timezone='UTC')
    household.save(actor=actor)
    baby = BabyProfile(household=household, name='Synthetic baby')
    baby.save(actor=actor)
    event = Event(
        baby=baby,
        occurred_at=datetime(2026, 6, 15, 15, tzinfo=UTC),
    )
    event.save(actor=actor)
    return event, actor


def test_session_isolation_is_read_committed():
    with connection.cursor() as cursor:
        cursor.execute('SHOW transaction_isolation')
        isolation = cursor.fetchone()[0]
    assert isolation == 'read committed'


def test_second_transaction_cannot_write_a_locked_stale_version():
    """A second connection hits the row lock, then a stale version matches nothing."""
    event, actor = _event()
    other = connections.create_connection('default')
    try:
        with transaction.atomic():
            locked = Event.all_objects.select_for_update().get(pk=event.pk)
            with other.cursor() as cursor:
                cursor.execute("SET lock_timeout = '300ms'")
                with pytest.raises(OperationalError, match='lock timeout'):
                    cursor.execute(
                        f'UPDATE {Event._meta.db_table} '
                        'SET version = version + 1 '
                        'WHERE id = %s AND version = %s',
                        [event.pk, locked.version],
                    )
            locked.occurred_at = locked.occurred_at + timedelta(minutes=5)
            locked.save(actor=actor, expected_version=1)
        other.rollback()
        with other.cursor() as cursor:
            cursor.execute(
                f'UPDATE {Event._meta.db_table} '
                'SET version = version + 1 '
                'WHERE id = %s AND version = 1',
                [event.pk],
            )
            assert cursor.rowcount == 0
            cursor.execute(
                f'SELECT version, occurred_at FROM {Event._meta.db_table} WHERE id = %s',
                [event.pk],
            )
            version, occurred_at = cursor.fetchone()
        assert version == 2
        assert occurred_at == datetime(2026, 6, 15, 15, 5, tzinfo=UTC)
    finally:
        other.close()


def test_two_concurrent_updates_let_one_writer_win():
    event, actor = _event()
    pk = event.pk
    barrier = threading.Barrier(2)
    outcomes = []
    lock = threading.Lock()

    def attempt(minute):
        entered = False
        try:
            row = Event.objects.get(pk=pk)
            assert row.version == 1
            entered = True
            barrier.wait(timeout=10)
            row.occurred_at = datetime(2026, 6, 15, 15, minute, tzinfo=UTC)
            row.save(actor=actor, expected_version=1)
            result = 'saved'
        except VersionConflict as exc:
            result = ('conflict', exc.current_version)
        except Exception as exc:  # noqa: BLE001 - report the thread failure
            if not entered:
                barrier.abort()
            result = exc
        finally:
            connections.close_all()
        with lock:
            outcomes.append((minute, result))

    threads = [
        threading.Thread(target=attempt, args=(1,)),
        threading.Thread(target=attempt, args=(2,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    saved = [minute for minute, result in outcomes if result == 'saved']
    conflicts = [item for item in outcomes if isinstance(item[1], tuple)]
    assert saved == [1] or saved == [2]
    assert len(conflicts) == 1
    assert conflicts[0][1] == ('conflict', 2)
    assert not isinstance(outcomes[0][1], Exception)
    stored = Event.objects.get(pk=pk)
    assert stored.version == 2
    assert stored.occurred_at == datetime(2026, 6, 15, 15, saved[0], tzinfo=UTC)
    assert stored.history().filter(action=AuditEntry.Action.UPDATE).count() == 1
