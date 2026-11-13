"""Encrypt a PostgreSQL custom-format dump.

The encryption key is read from the environment and is never printed.
Ciphertext is AES-256-CBC via openssl. A backup cannot be read without the key.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404
from pathlib import Path

_PG_DUMP_VERSION = re.compile(r'PostgreSQL\)\s+(\d+)')


def pg_dump_major(version_text: str) -> int:
    """Major version from `pg_dump --version` (for example `pg_dump (PostgreSQL) 16.1`)."""
    match = _PG_DUMP_VERSION.search(version_text)
    if match is None:
        raise ValueError('unrecognized pg_dump version')
    return int(match.group(1))


def client_can_dump_server(client_major: int, server_major: int) -> bool:
    """pg_dump refuses to dump a newer server major."""
    return client_major >= server_major


def _openssl(args: list[str], payload: bytes, key: str) -> bytes:
    openssl = shutil.which('openssl')
    if openssl is None:
        raise RuntimeError('openssl is not installed')
    completed = subprocess.run(  # nosec B603
        [openssl, *args, '-pass', 'env:BACKUP_ENCRYPTION_KEY'],
        input=payload,
        capture_output=True,
        check=False,
        env={**os.environ, 'BACKUP_ENCRYPTION_KEY': key},
    )
    if completed.returncode != 0:
        raise RuntimeError('openssl backup command failed')
    return completed.stdout


def encrypt(payload: bytes, key: str) -> bytes:
    if not key:
        raise RuntimeError('BACKUP_ENCRYPTION_KEY is empty')
    return _openssl(
        ['enc', '-aes-256-cbc', '-pbkdf2', '-salt'],
        payload,
        key,
    )


def decrypt(payload: bytes, key: str) -> bytes:
    return _openssl(
        ['enc', '-d', '-aes-256-cbc', '-pbkdf2'],
        payload,
        key,
    )


def purge_old(directory: Path, retention_days: int, *, now_ts: float) -> int:
    removed = 0
    cutoff = now_ts - retention_days * 86400
    if not directory.exists():
        return 0
    for path in directory.glob('*.sql.enc'):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed
