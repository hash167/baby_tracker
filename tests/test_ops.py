"""Probes, alert windows, backup encryption, and manifest hygiene."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from config.middleware import ContentSecurityPolicyMiddleware
from tracker.alerts import sustained
from tracker.backup import decrypt, encrypt
from tracker.collector_policy import (
    SAFE_KEYS,
    allowlist_from_config,
    config_text,
    redact,
)
from tracker.health import database_ready

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[1]


def test_healthz_does_not_query_and_readyz_hides_failures(client, monkeypatch):
    with CaptureQueriesContext(connection) as captured:
        health = client.get('/healthz')
    assert health.status_code == 200
    assert health.content == b'ok'
    assert captured.captured_queries == []

    ready = client.get('/readyz', HTTP_HOST='127.0.0.1')
    assert ready.status_code == 200
    assert ready.content == b'ok'

    monkeypatch.setattr(
        'tracker.health.database_ready', lambda timeout, ping=None: False
    )
    down = client.get('/readyz')
    assert down.status_code == 503
    assert down.content == b'unavailable'
    assert b'password' not in down.content
    assert b'postgres' not in down.content.lower()

    def slow():
        time.sleep(1)
        return True

    assert database_ready(0.05, ping=slow) is False


def test_foreign_host_is_rejected(client):
    response = client.get('/login/', HTTP_HOST='evil.example')
    assert response.status_code == 400


def test_probes_answer_for_a_pod_ip_host(client):
    """Kubelet sends the pod IP as Host. OpenTelemetry must not reject that first."""
    from django.conf import settings

    assert settings.MIDDLEWARE[0] == 'config.middleware.HealthzMiddleware'
    health = client.get('/healthz', HTTP_HOST='10.1.2.3')
    assert health.status_code == 200
    assert health.content == b'ok'
    ready = client.get('/readyz', HTTP_HOST='10.1.2.3')
    assert ready.status_code == 200
    assert ready.content == b'ok'


def test_content_security_policy_header():
    def app(_request):
        from django.http import HttpResponse

        return HttpResponse('ok')

    response = ContentSecurityPolicyMiddleware(app)(RequestFactory().get('/'))
    policy = response['Content-Security-Policy']
    assert "default-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert response['Referrer-Policy'] == 'same-origin'


def test_alert_window_ignores_a_single_blip():
    assert (
        sustained(
            [(0, 9), (30, 0), (60, 0)],
            window_seconds=300,
            threshold=5,
            minimum=3,
        )
        is False
    )
    assert (
        sustained(
            [(0, 9), (30, 8), (60, 7)],
            window_seconds=300,
            threshold=5,
            minimum=3,
        )
        is True
    )


def test_pg_dump_must_be_at_least_the_server_major():
    from tracker.backup import client_can_dump_server, pg_dump_major

    assert pg_dump_major('pg_dump (PostgreSQL) 16.11') == 16
    assert pg_dump_major('pg_dump (PostgreSQL) 15.19') == 15
    assert client_can_dump_server(16, 16) is True
    assert client_can_dump_server(16, 15) is True
    assert client_can_dump_server(15, 16) is False


def test_migrate_script_finds_config_when_run_by_path(tmp_path):
    """The Job runs `python /app/docker/migrate.py`, not `python -m`."""
    import os
    import subprocess  # nosec B404
    import sys

    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env['DJANGO_SETTINGS_MODULE'] = 'config.settings.production'
    env['DJANGO_SECRET_KEY'] = 'deploy-check-placeholder-not-a-secret'  # nosec B105
    env['DJANGO_ALLOWED_HOSTS'] = 'localhost'
    env.setdefault('POSTGRES_HOST', '127.0.0.1')
    env.setdefault('POSTGRES_PORT', '5432')
    env.setdefault('POSTGRES_DB', 'baby_tracker')
    env.setdefault('POSTGRES_USER', 'baby_tracker')
    env.setdefault('POSTGRES_PASSWORD', 'baby_tracker')
    script = ROOT / 'docker' / 'migrate.py'
    result = subprocess.run(  # nosec B603
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert 'No module named' not in result.stderr + result.stdout


def test_backup_ciphertext_needs_the_key():
    secret = b'synthetic-dump-bytes'
    key = 'synthetic-backup-key'
    blob = encrypt(secret, key)
    assert secret not in blob
    assert decrypt(blob, key) == secret
    with pytest.raises(RuntimeError):
        decrypt(blob, 'other-synthetic-key')


def test_collector_allowlist_drops_sensitive_attributes():
    allowed = allowlist_from_config(config_text())
    assert tuple(allowed) == SAFE_KEYS
    cleaned = redact(
        {
            'http.request.method': 'POST',
            'http.route': '/events/{event_id}/',
            'app.event_type': 'diaper',
            'note': 'SyntheticNoteAlpha9f3c',
            'user.email': 'parent.synthetic@example.com',
            'db.statement': 'select SyntheticBabyNameQq7',
            'url.full': 'https://example.test/feed?side=left',
        },
        allowed,
    )
    blob = str(cleaned)
    assert 'SyntheticNoteAlpha9f3c' not in blob
    assert 'parent.synthetic@example.com' not in blob
    assert 'SyntheticBabyNameQq7' not in blob
    assert cleaned['http.route'] == '/events/{event_id}/'


def test_manifests_have_no_secret_values():
    text = '\n'.join(
        path.read_text()
        for path in (ROOT / 'deploy').rglob('*.yaml')
        if 'REPLACE' in path.read_text() or 'secretKeyRef' in path.read_text() or True
    )
    lowered = text.lower()
    assert 'akid' not in lowered
    assert 'begin private key' not in lowered
    assert 'honeycomb_api_key:' not in lowered
    for path in (ROOT / 'deploy' / 'k8s').glob('*.yaml'):
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith('password:'):
                raise AssertionError(path.name)
            if 'POSTGRES_PASSWORD:' in line and 'key:' not in line:
                raise AssertionError(path.name)
    app = (ROOT / 'deploy' / 'k8s' / 'app.yaml').read_text()
    assert 'path: /readyz' in app
    assert 'path: /healthz' in app
    assert 'nodePort: 30820' in app
    assert 'nodeSelector' not in app
    assert 'baby-tracker-imports' not in app
    assert 'emptyDir' not in app
    assert 'maxSkew: 1' in app
    assert 'ScheduleAnyway' in app
    assert 'weight: 100' in app
    assert 'REPLACE_SHA' in app
    assert 'baby-tracker-db' in app
    postgres = (ROOT / 'deploy' / 'k8s' / 'postgres.yaml').read_text()
    assert 'postgres:16' in postgres
    assert 'postgres:15' not in postgres
    config = (ROOT / 'deploy' / 'k8s' / 'configmap.yaml').read_text()
    assert 'baby-tracker.baby-tracker.svc.cluster.local' in config
    assert '100.67.' not in config
    assert 'OTEL_EXPORTER_OTLP_ENDPOINT' not in config
