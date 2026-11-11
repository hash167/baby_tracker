"""Encrypt a PostgreSQL custom-format dump.

The encryption key is read from the environment and is never printed.
Ciphertext is AES-256-CBC via openssl. A backup cannot be read without the key.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
from pathlib import Path


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
