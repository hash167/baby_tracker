import os
from pathlib import Path

from .base import *

SECRET_KEY = os.environ['DJANGO_SECRET_KEY']
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ['DJANGO_ALLOWED_HOSTS'].split(',')
    if host.strip()
]
DEBUG = False

STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# Persist SQLite on a mounted volume (default /app/data). Prefer a single
# gunicorn worker and a single replica; locks and lost writes are likely with more.
_db_path = Path(os.environ.get('DJANGO_DB_PATH', BASE_DIR / 'data' / 'db.sqlite3'))
_db_path.parent.mkdir(parents=True, exist_ok=True)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': _db_path,
    }
}

# Default: plain HTTP (local docker / direct port publish).
# Behind a TLS-terminating proxy that *overwrites* X-Forwarded-Proto, set
# DJANGO_BEHIND_PROXY=true to enable redirect, secure cookies, and the proxy header
# together. Do not expose gunicorn publicly when that is enabled.
_behind_proxy = os.environ.get('DJANGO_BEHIND_PROXY', '').lower() in {
    '1',
    'true',
    'yes',
}
if _behind_proxy:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_REDIRECT_EXEMPT = [r'^healthz$']
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
