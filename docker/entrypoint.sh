#!/bin/sh
set -eu

# Start as root so a mounted volume can be chowned, then drop to app.
if [ "$(id -u)" = "0" ]; then
  DATA_DIR="$(dirname "${DJANGO_DB_PATH:-/app/data/db.sqlite3}")"
  mkdir -p "$DATA_DIR"
  chown -R app:app "$DATA_DIR"
  exec runuser -u app -- /app/docker/entrypoint.sh "$@"
fi

python manage.py migrate --noinput
exec "$@"
