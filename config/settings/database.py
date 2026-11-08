"""PostgreSQL-only database configuration.

SQLite and other engines are unsupported for local, test, and production.
"""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

POSTGRES_ENGINE = 'django.db.backends.postgresql'


def postgres_database_from_env() -> dict:
    """Build a Django DATABASES['default'] entry from POSTGRES_* env vars."""
    return {
        'ENGINE': POSTGRES_ENGINE,
        'NAME': os.environ.get('POSTGRES_DB', 'baby_tracker'),
        'USER': os.environ.get('POSTGRES_USER', 'baby_tracker'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'baby_tracker'),
        'HOST': os.environ.get('POSTGRES_HOST', '127.0.0.1'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'CONN_MAX_AGE': int(os.environ.get('POSTGRES_CONN_MAX_AGE', '0')),
        'OPTIONS': {},
        'TEST': {
            # pytest-django creates/destroys this DB each run unless --reuse-db.
            'NAME': os.environ.get('POSTGRES_TEST_DB', 'test_baby_tracker'),
        },
    }


def require_postgresql(databases: dict) -> None:
    """Raise if the default database is not PostgreSQL."""
    engine = databases.get('default', {}).get('ENGINE', '')
    if engine != POSTGRES_ENGINE:
        raise ImproperlyConfigured(
            'This project requires PostgreSQL '
            f'({POSTGRES_ENGINE!r}). Got ENGINE={engine!r}. '
            'SQLite and other database engines are not supported.'
        )
