"""PostgreSQL-only database configuration.

SQLite and other engines are unsupported for local, test, and production.
"""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

POSTGRES_ENGINE = 'django.db.backends.postgresql'

_LOCAL_DEFAULTS = {
    'POSTGRES_DB': 'baby_tracker',
    'POSTGRES_USER': 'baby_tracker',
    'POSTGRES_PASSWORD': 'baby_tracker',
    'POSTGRES_HOST': '127.0.0.1',
}


def _env(name: str, *, allow_defaults: bool) -> str:
    if name in os.environ and os.environ[name] != '':
        return os.environ[name]
    if allow_defaults and name in _LOCAL_DEFAULTS:
        return _LOCAL_DEFAULTS[name]
    raise ImproperlyConfigured(
        f'{name} must be set for PostgreSQL connections in this environment.'
    )


def postgres_database_from_env(*, allow_defaults: bool = False) -> dict:
    """Build a Django DATABASES['default'] entry from POSTGRES_* env vars.

    Local settings may pass allow_defaults=True (compose-compatible defaults).
    Production must leave allow_defaults=False so missing credentials fail fast.
    """
    connect_timeout = int(os.environ.get('POSTGRES_CONNECT_TIMEOUT', '5'))
    return {
        'ENGINE': POSTGRES_ENGINE,
        'NAME': _env('POSTGRES_DB', allow_defaults=allow_defaults),
        'USER': _env('POSTGRES_USER', allow_defaults=allow_defaults),
        'PASSWORD': _env('POSTGRES_PASSWORD', allow_defaults=allow_defaults),
        'HOST': _env('POSTGRES_HOST', allow_defaults=allow_defaults),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'CONN_MAX_AGE': int(os.environ.get('POSTGRES_CONN_MAX_AGE', '0')),
        'OPTIONS': {
            'connect_timeout': connect_timeout,
        },
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
