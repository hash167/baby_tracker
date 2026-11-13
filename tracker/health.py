"""Process and database probes.

`/healthz` does not touch PostgreSQL. `/readyz` runs one `SELECT 1` with a
bounded timeout and returns no connection details.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

from django.conf import settings
from django.db import connections
from django.http import HttpResponse


def healthz(_request):
    return HttpResponse('ok', content_type='text/plain')


def readyz(_request):
    timeout = float(settings.READY_TIMEOUT_SECONDS)
    if database_ready(timeout):
        return HttpResponse('ok', content_type='text/plain')
    return HttpResponse('unavailable', status=503, content_type='text/plain')


def _ping(timeout_ms: int) -> bool:
    connection = connections['default']
    try:
        with connection.cursor() as cursor:
            cursor.execute('SET statement_timeout = %s', [timeout_ms])
            cursor.execute('SELECT 1')
            cursor.fetchone()
        return True
    finally:
        connection.close()


def database_ready(timeout: float, ping=None) -> bool:
    timeout_ms = max(1, int(timeout * 1000))
    check = ping or (lambda: _ping(timeout_ms))
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(check)
        try:
            return bool(future.result(timeout=timeout + 0.5))
        except FuturesTimeout:
            return False
        except Exception:
            return False
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
