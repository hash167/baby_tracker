"""Dump, encrypt, and retain PostgreSQL backups. Exit 1 on failure."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from config.telemetry import record_functional_attributes
from tracker.backup import encrypt, purge_old


class Command(BaseCommand):
    help = 'Write an encrypted pg_dump into BACKUP_DIR.'

    def handle(self, *args, **options):
        started = time.monotonic()
        key = os.environ.get('BACKUP_ENCRYPTION_KEY', '')
        if not key:
            record_functional_attributes(event_type='backup', outcome='failure')
            raise CommandError('BACKUP_ENCRYPTION_KEY is not set')
        directory = Path(os.environ.get('BACKUP_DIR', settings.BACKUP_DIR))
        directory.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update(
            {
                'PGHOST': os.environ.get('POSTGRES_HOST', ''),
                'PGPORT': os.environ.get('POSTGRES_PORT', '5432'),
                'PGUSER': os.environ.get('POSTGRES_USER', ''),
                'PGPASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
                'PGDATABASE': os.environ.get('POSTGRES_DB', ''),
            }
        )
        pg_dump = shutil.which('pg_dump')
        if pg_dump is None:
            record_functional_attributes(event_type='backup', outcome='failure')
            raise CommandError('pg_dump is not installed')
        dump = subprocess.run(  # nosec B603
            [pg_dump, '--format=custom', '--no-owner'],
            capture_output=True,
            check=False,
            env=env,
        )
        if dump.returncode != 0:
            record_functional_attributes(event_type='backup', outcome='failure')
            raise CommandError('pg_dump failed')
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        target = directory / f'baby-tracker-{stamp}.sql.enc'
        target.write_bytes(encrypt(dump.stdout, key))
        retention = int(os.environ.get('BACKUP_RETENTION_DAYS', '14'))
        purge_old(directory, retention, now_ts=time.time())
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record_functional_attributes(event_type='backup', outcome='success')
        self.stdout.write(f'backup outcome=success duration_ms={elapsed_ms}')
