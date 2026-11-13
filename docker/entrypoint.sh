#!/bin/sh
set -eu

# Migrations are a separate command (`python /app/docker/migrate.py`), not
# part of process start. Prepare writable directories, then drop root.
if [ "$(id -u)" = "0" ]; then
  backup_root="${BACKUP_DIR:-/var/lib/baby-tracker/backups}"
  mkdir -p "$backup_root"
  chown -R app:app "$backup_root"
  exec runuser -u app -- /app/docker/entrypoint.sh "$@"
fi

exec "$@"
