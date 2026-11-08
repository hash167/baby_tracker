#!/bin/sh
set -eu

DATA_DIR="$(dirname "${DJANGO_DB_PATH:-/app/data/db.sqlite3}")"

# Start as root so a mounted volume can be chowned, then drop to app.
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown -R app:app "$DATA_DIR"
  exec runuser -u app -- /app/docker/entrypoint.sh "$@"
fi

# Exclusive lock so overlapping container starts do not race migrate on SQLite.
# Still run only one replica against this database file.
mkdir -p "$DATA_DIR"
flock "$DATA_DIR/.migrate.lock" python manage.py migrate --noinput
exec "$@"
