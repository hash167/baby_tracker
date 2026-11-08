from .base import *
import os


SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
ALLOWED_HOSTS = os.environ["DJANGO_ALLOWED_HOSTS"].split(",")
DEBUG = False

STATIC_ROOT = BASE_DIR / "staticfiles"
