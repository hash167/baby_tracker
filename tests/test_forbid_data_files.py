"""Path policy for CSV exports and database dumps. No file contents."""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import forbid_data_files  # noqa: E402


@pytest.mark.parametrize(
    ('path', 'reason'),
    [
        ('tests/fixtures/synthetic.csv', None),
        ('tests/fixtures/nested/synthetic.csv', None),
        ('tests/fixtures/synthetic.CSV', None),
        ('tests/fixtures/synthetic.csv.gz', None),
        ('exports/nara.csv', 'csv export outside tests/fixtures/'),
        ('tests/fixtures.csv', 'csv export outside tests/fixtures/'),
        ('tests/fixtures/../outside.csv', 'csv export outside tests/fixtures/'),
        ('notes.txt', None),
        ('tests/fixtures/backup.sql', 'database dump'),
        ('backups/baby.sql.gz', 'database dump'),
        ('db/app.dump', 'database dump'),
        ('db/app.sqlite3', 'database dump'),
        ('local/app.db', 'database dump'),
    ],
)
def test_forbidden_reason(path, reason):
    assert forbid_data_files.forbidden_reason(path, ROOT) == reason


def test_repository_tree_has_no_forbidden_files():
    result = subprocess.run(  # nosec B603
        [sys.executable, str(ROOT / 'scripts' / 'forbid_data_files.py'), '--all'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == ''
