#!/bin/sh
set -eu

# Drop root after any future privileged setup (volume prep, etc.).
if [ "$(id -u)" = "0" ]; then
  exec runuser -u app -- /app/docker/entrypoint.sh "$@"
fi

# Serialize migrate within a single host/container start. Use one app replica
# against a given database, or a dedicated migrate job, in real deployments.
flock /tmp/baby_tracker_migrate.lock python manage.py migrate --noinput
exec "$@"
