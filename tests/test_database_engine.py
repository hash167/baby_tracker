import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.settings.database import POSTGRES_ENGINE, require_postgresql


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
