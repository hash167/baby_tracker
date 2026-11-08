import os

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.settings.database import (
    POSTGRES_ENGINE,
    postgres_database_from_env,
    require_postgresql,
)


def test_default_database_engine_is_postgresql():
    assert settings.DATABASES['default']['ENGINE'] == POSTGRES_ENGINE


def test_require_postgresql_rejects_sqlite():
    with pytest.raises(ImproperlyConfigured, match='PostgreSQL'):
        require_postgresql(
            {
                'default': {
                    'ENGINE': 'django.db.backends.sqlite3',
                    'NAME': ':memory:',
                }
            }
        )


def test_local_defaults_fill_missing_postgres_env(monkeypatch):
    for key in (
        'POSTGRES_DB',
        'POSTGRES_USER',
        'POSTGRES_PASSWORD',
        'POSTGRES_HOST',
        'POSTGRES_PORT',
    ):
        monkeypatch.delenv(key, raising=False)

    db = postgres_database_from_env(allow_defaults=True)
    assert db['NAME'] == 'baby_tracker'
    assert db['USER'] == 'baby_tracker'
    assert db['PASSWORD'] == 'baby_tracker'
    assert db['HOST'] == '127.0.0.1'
    assert db['PORT'] == '5432'


def test_production_requires_postgres_credentials(monkeypatch):
    # CI exports POSTGRES_PORT. Clear it so the default-port assertion
    # checks the unset path, not an ambient value that happens to be 5432.
    for key in (
        'POSTGRES_DB',
        'POSTGRES_USER',
        'POSTGRES_PASSWORD',
        'POSTGRES_HOST',
        'POSTGRES_PORT',
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ImproperlyConfigured, match='POSTGRES_DB'):
        postgres_database_from_env(allow_defaults=False)

    monkeypatch.setenv('POSTGRES_DB', 'prod_db')
    with pytest.raises(ImproperlyConfigured, match='POSTGRES_USER'):
        postgres_database_from_env(allow_defaults=False)

    monkeypatch.setenv('POSTGRES_USER', 'prod_user')
    monkeypatch.setenv('POSTGRES_PASSWORD', 'prod_secret')
    monkeypatch.setenv('POSTGRES_HOST', 'db.internal')
    db = postgres_database_from_env(allow_defaults=False)
    assert db['NAME'] == 'prod_db'
    assert db['USER'] == 'prod_user'
    assert db['PASSWORD'] == 'prod_secret'
    assert db['HOST'] == 'db.internal'
    assert os.environ.get('POSTGRES_PORT') is None
    assert db['PORT'] == '5432'

    monkeypatch.setenv('POSTGRES_PORT', '6543')
    db = postgres_database_from_env(allow_defaults=False)
    assert db['PORT'] == '6543'
