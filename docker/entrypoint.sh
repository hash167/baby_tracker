#!/bin/sh
set -eu

uv run python manage.py migrate --noinput
exec "$@"
