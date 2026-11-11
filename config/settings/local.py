import os

from .base import *
from .database import postgres_database_from_env, require_postgresql

DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1']

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-local-dev-only-do-not-use-in-production',
)

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

DATABASES = {
    'default': postgres_database_from_env(allow_defaults=True),
}
require_postgresql(DATABASES)

# Set DJANGO_DEBUG_TOOLBAR=0 for browser tests. The toolbar covers phone layouts.
if os.environ.get('DJANGO_DEBUG_TOOLBAR', '1') != '0':
    INSTALLED_APPS += [
        'debug_toolbar',
    ]
    MIDDLEWARE += [
        'debug_toolbar.middleware.DebugToolbarMiddleware',
    ]

INTERNAL_IPS = [
    '127.0.0.1',
]
