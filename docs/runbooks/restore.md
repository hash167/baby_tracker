# Encrypted backups and restore

The CronJob `baby-tracker-backup` runs `python manage.py backup_database` daily. It writes an AES-256-CBC file (`*.sql.enc`) to the backup volume. The key is the `BACKUP_ENCRYPTION_KEY` value in the Secret `baby-tracker-backup`. A backup file cannot be read without that key. The key is not in git.

Retention is `BACKUP_RETENTION_DAYS` (default 14). A failed run exits non-zero, which leaves the Job failed. The alert rule `backup_missing` fires when a successful backup span has not arrived. See `docs/runbooks/observability.md`.

## Restore into a separate database

Run this on a machine that has the key and the backup file, and that can reach PostgreSQL. Do not paste row contents into an issue or a log.

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
  -in baby-tracker-TIMESTAMP.sql.enc -out /tmp/restore.dump
createdb baby_tracker_restore
pg_restore --no-owner --dbname baby_tracker_restore /tmp/restore.dump
rm /tmp/restore.dump
```

Compare `SELECT COUNT(*)` for `tracker_event` and `auth_user` with the source database. Start the app against `baby_tracker_restore` only long enough to see `/readyz` return `ok`. Drop the restore database when the drill is finished.

## Dev drill

On 2026-11-11 a drill against the local synthetic database encrypted a `pg_dump` custom archive, decrypted it with the same key, restored it into a separate database, and compared row counts. Counts matched. The wrong key did not decrypt the file. No dump file was committed. A production drill against the live cluster is still required the first time the CronJob runs there; this repository cannot reach that cluster.
