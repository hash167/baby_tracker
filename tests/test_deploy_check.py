"""`manage.py check --deploy` against production settings."""

from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'deploy_check.sh'


def test_deploy_check_passes_with_production_settings():
    result = subprocess.run(  # nosec B603
        [str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_deploy_check_fails_without_proxy_hardening():
    result = subprocess.run(  # nosec B603
        [str(SCRIPT), '--without-proxy'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert 'security.W004' in output
    assert 'security.W008' in output
    assert 'security.W012' in output
    assert 'security.W016' in output
