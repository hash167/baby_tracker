from .base import *

DEBUG = True

ALLOWED_HOSTS = ['*']

INSTALLED_APPS += [
    'debug_toolbar',
]

MIDDLEWARE += [
    'debug_toolbar.middleware.DebugToolbarMiddleware',
]

SECRET_KEY = 'django-insecure-z87oe=6ih^c0nkbq-o=cub96q)=xw*=bxnqdbj)pznl_rgblgu'