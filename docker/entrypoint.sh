#!/bin/sh
set -eu

# Drop root after any future privileged setup (volume prep, etc.).
if [ "$(id -u)" = "0" ]; then
  exec runuser -u app -- /app/docker/entrypoint.sh "$@"
fi

# Wait for Postgres, serialize migrate with a Postgres advisory lock, then start.
python /app/docker/migrate.py
exec "$@"
