#!/usr/bin/env python3
"""Reject CSV exports outside tests/fixtures and any database dump.

The script looks at paths only. It does not read file contents.

Synthetic CSV fixtures may live under tests/fixtures/. Database dumps are
rejected everywhere, including that directory.
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404
import sys
from pathlib import Path

CSV_SUFFIXES = (
    '.csv',
    '.csv.gz',
    '.csv.bz2',
    '.csv.xz',
    '.csv.zip',
)
DUMP_SUFFIXES = (
    '.sql',
    '.sql.gz',
    '.sql.bz2',
    '.sql.xz',
    '.sql.zip',
    '.dump',
    '.dump.gz',
    '.dump.bz2',
    '.dump.xz',
    '.pgdump',
    '.pgdump.gz',
    '.pgsql',
    '.psql',
    '.sqlite',
    '.sqlite3',
    '.db',
)
FIXTURE_PREFIX = 'tests/fixtures/'


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def relative_posix(path: str, root: Path) -> str:
    raw = Path(path)
    if raw.is_absolute():
        try:
            return raw.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return raw.as_posix()
    parts: list[str] = []
    for part in raw.parts:
        if part in ('.', ''):
            continue
        if part == '..':
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return '/'.join(parts)


def _matched_suffix(name: str, suffixes: tuple[str, ...]) -> str | None:
    lowered = name.lower()
    for suffix in sorted(suffixes, key=len, reverse=True):
        if lowered.endswith(suffix):
            return suffix
    return None


def forbidden_reason(path: str, root: Path | None = None) -> str | None:
    """Return why a path is forbidden, or None when it may be committed."""
    root = root or repo_root()
    relative = relative_posix(path, root)
    name = relative.rsplit('/', 1)[-1]
    if _matched_suffix(name, DUMP_SUFFIXES):
        return 'database dump'
    if _matched_suffix(name, CSV_SUFFIXES) and not relative.startswith(FIXTURE_PREFIX):
        return 'csv export outside tests/fixtures/'
    return None


def list_repo_files(root: Path) -> list[str]:
    # Fixed git argv, not a shell string. Line suppresses B603 and B607;
    # Bandit keeps only one id from a single nosec comment.
    output = subprocess.check_output(  # nosec
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=root,
    )
    return [item.decode() for item in output.split(b'\0') if item]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--all',
        action='store_true',
        help='check tracked files and untracked files that are not ignored',
    )
    parser.add_argument('paths', nargs='*')
    args = parser.parse_args(argv)
    root = repo_root()

    if args.all and args.paths:
        print('pass either --all or paths, not both', file=sys.stderr)
        return 2
    if args.all:
        paths = list_repo_files(root)
    elif args.paths:
        paths = args.paths
    else:
        print('pass --all or at least one path', file=sys.stderr)
        return 2

    failures: list[tuple[str, str]] = []
    for path in paths:
        reason = forbidden_reason(path, root)
        if reason:
            failures.append((relative_posix(path, root), reason))
    if not failures:
        return 0
    for path, reason in failures:
        print(f'forbidden data file: {path} ({reason})', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
