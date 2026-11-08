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
# gunicorn worker with SQLite; locks and lost writes are likely with more.
_db_path = Path(os.environ.get('DJANGO_DB_PATH', BASE_DIR / 'data' / 'db.sqlite3'))
_db_path.parent.mkdir(parents=True, exist_ok=True)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': _db_path,
    }
}

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = os.environ.get('DJANGO_SECURE_SSL_REDIRECT', 'true').lower() in {
    '1',
    'true',
    'yes',
}
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
