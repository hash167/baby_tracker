import os

from .base import *
from .database import postgres_database_from_env, require_postgresql

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

DATABASES = {
    # No credential defaults — missing POSTGRES_* must fail closed.
    'default': postgres_database_from_env(allow_defaults=False),
}
require_postgresql(DATABASES)

# Default: plain HTTP (local docker / direct port publish).
# Behind a TLS-terminating proxy that *overwrites* X-Forwarded-Proto, set
# DJANGO_BEHIND_PROXY=true to enable redirect, secure cookies, HSTS, and the
# proxy header together. Do not expose gunicorn publicly when that is enabled.
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
    SECURE_REDIRECT_EXEMPT = [r'^healthz$', r'^readyz$']
    # Required for `check --deploy` once TLS terminates in front of gunicorn.
    # The public hostname, CSRF_TRUSTED_ORIGINS, and CSP stay with issue #36.
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
]

MIDDLEWARE += ['config.middleware.ContentSecurityPolicyMiddleware']
