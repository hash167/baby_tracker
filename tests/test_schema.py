"""PostgreSQL column types and indexes created by the tracker migrations."""

from __future__ import annotations

import pytest
from django.db import connection

from tests.factories import CaregiverFactory
from tracker.models import AuditEntry, Event, Household

pytestmark = pytest.mark.django_db


def _column_type(table: str, column: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
            """,
            [table, column],
        )
        row = cursor.fetchone()
    assert row is not None, f'{table}.{column} is missing'
    return row[0]


def test_event_instants_are_timestamptz_and_audit_changes_are_jsonb():
    assert (
        _column_type(Event._meta.db_table, 'occurred_at') == 'timestamp with time zone'
    )
    assert (
        _column_type(Event._meta.db_table, 'recorded_at') == 'timestamp with time zone'
    )
    assert (
        _column_type(Event._meta.db_table, 'deleted_at') == 'timestamp with time zone'
    )
    assert (
        _column_type(Household._meta.db_table, 'deleted_at')
        == 'timestamp with time zone'
    )
    assert _column_type(AuditEntry._meta.db_table, 'changes') == 'jsonb'
    assert _column_type(AuditEntry._meta.db_table, 'at') == 'timestamp with time zone'


def test_partial_indexes_and_version_checks_exist():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname IN ('event_active_hh_time', 'event_source_activity_uq')
            """
        )
        indexes = dict(cursor.fetchall())
        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conname IN (
                'household_version_gte_1',
                'baby_version_gte_1',
                'event_version_gte_1',
                'event_source_system_set'
            )
            """
        )
        checks = {row[0] for row in cursor.fetchall()}

    active = indexes['event_active_hh_time']
    assert 'deleted_at IS NULL' in active
    source = indexes['event_source_activity_uq']
    assert 'UNIQUE' in source.upper()
    assert 'source_activity_id' in source
    assert checks == {
        'household_version_gte_1',
        'baby_version_gte_1',
        'event_version_gte_1',
        'event_source_system_set',
    }


def test_default_managers_hide_deleted_rows():
    # Touch the database so the model registry is fully loaded under pytest-django.
    CaregiverFactory()
    assert Household._default_manager is Household.objects
    assert Household._base_manager is Household.all_objects
    active_sql = str(Household.objects.all().query)
    assert 'WHERE' in active_sql
    assert 'deleted_at' in active_sql.split('WHERE', 1)[1]
    every_sql = str(Household.all_objects.all().query)
    where = every_sql.split('WHERE', 1)[1] if 'WHERE' in every_sql else ''
    assert 'deleted_at' not in where
