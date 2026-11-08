"""Wait for PostgreSQL, take a cluster-wide advisory lock, then migrate.

Used by the container entrypoint so concurrent replicas do not race schema
changes. The lock is held in PostgreSQL, not on a local /tmp file.
"""

from __future__ import annotations

import os
import sys
import time

import django
from django.core.management import call_command
from django.db import connection
from django.db.utils import OperationalError

# Stable key for baby_tracker schema migrations across replicas.
MIGRATE_LOCK_KEY = 0xBA87_7A6B
READY_TIMEOUT = int(os.environ.get('POSTGRES_READY_TIMEOUT', '60'))
READY_INTERVAL = float(os.environ.get('POSTGRES_READY_INTERVAL', '1'))


def wait_for_database() -> None:
    deadline = time.monotonic() + READY_TIMEOUT
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            connection.ensure_connection()
            return
        except OperationalError as exc:
            last_error = exc
            connection.close()
            time.sleep(READY_INTERVAL)
    raise SystemExit(f'PostgreSQL not ready after {READY_TIMEOUT}s: {last_error}')


def main() -> int:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')
    django.setup()
    wait_for_database()

    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_lock(%s)', [MIGRATE_LOCK_KEY])
    try:
        call_command('migrate', interactive=False, verbosity=1)
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [MIGRATE_LOCK_KEY])
        connection.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
