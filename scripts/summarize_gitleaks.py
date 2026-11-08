#!/usr/bin/env python3
"""Print a gitleaks result without secret values, match text, or commit messages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(path: Path) -> list[dict]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    data = json.loads(path.read_text(encoding='utf-8'))
    if data is None:
        return []
    if not isinstance(data, list):
        raise SystemExit(f'{path} is not a gitleaks JSON list')
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--status', type=int, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()

    findings = _load(args.report)
    if args.status == 0 and not findings:
        print(f'{args.label}: clean')
        return 0

    # Print only. The caller decides the process status from gitleaks' exit code.
    print(
        f'{args.label}: {len(findings)} finding(s), gitleaks exit {args.status}',
        file=sys.stderr,
    )
    for item in findings:
        commit = item.get('Commit') or ''
        short = commit[:12]
        rule = item.get('RuleID') or 'unknown-rule'
        filename = item.get('File') or 'unknown-file'
        line = item.get('StartLine')
        print(
            f'  rule={rule} file={filename} commit={short} line={line} secret=redacted',
            file=sys.stderr,
        )
    if args.status != 0 and not findings:
        print(
            f'{args.label}: gitleaks failed without a parseable finding',
            file=sys.stderr,
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
