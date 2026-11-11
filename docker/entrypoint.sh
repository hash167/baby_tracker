#!/bin/sh
set -eu

# Migrations are a separate command (`python /app/docker/migrate.py`), not
# part of process start. Prepare writable directories, then drop root.
if [ "$(id -u)" = "0" ]; then
  import_root="${IMPORT_ROOT:-/var/lib/baby-tracker/imports}"
  backup_root="${BACKUP_DIR:-/var/lib/baby-tracker/backups}"
  mkdir -p "$import_root" "$backup_root"
  chown -R app:app "$import_root" "$backup_root"
  exec runuser -u app -- /app/docker/entrypoint.sh "$@"
fi

exec "$@"
