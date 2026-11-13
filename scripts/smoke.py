#!/usr/bin/env python3
"""Post-deploy smoke check.

Reads BASE_URL. When SMOKE_USERNAME and SMOKE_PASSWORD are set, logs in,
creates one diaper, and deletes it. Prints status lines only.
"""

from __future__ import annotations

import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


def main() -> int:
    base = os.environ.get('BASE_URL', '').rstrip('/')
    if not base:
        print('BASE_URL is required', file=sys.stderr)
        return 2
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def open_url(url, data=None, headers=None):
        request = urllib.request.Request(url, data=data, headers=headers or {})
        try:
            with opener.open(request, timeout=20) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    status, body = open_url(f'{base}/healthz')
    if status != 200 or body.strip() != b'ok':
        print(f'healthz status={status}')
        return 1
    status, body = open_url(f'{base}/login/')
    if status != 200 or b'Log in' not in body:
        print(f'login status={status}')
        return 1
    username = os.environ.get('SMOKE_USERNAME', '')
    password = os.environ.get('SMOKE_PASSWORD', '')
    if not username or not password:
        print('smoke ok login+health')
        return 0
    token = _csrf(body)
    login_body = urllib.parse.urlencode(
        {
            'username': username,
            'password': password,
            'csrfmiddlewaretoken': token,
            'next': '/timeline/',
        }
    ).encode()
    status, body = open_url(
        f'{base}/login/',
        data=login_body,
        headers={
            'Referer': f'{base}/login/',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    )
    if status not in {200, 302} or b'Timeline' not in body and status != 302:
        print(f'authenticated login status={status}')
        return 1
    status, body = open_url(f'{base}/timeline/')
    if status != 200:
        print(f'timeline status={status}')
        return 1
    token = _csrf(body)
    created, created_body = open_url(
        f'{base}/diapers/',
        data=urllib.parse.urlencode(
            {
                'csrfmiddlewaretoken': token,
                'diaper_type': 'dry',
                'idempotency_key': f'smoke-{uuid.uuid4()}',
                'notes': '',
            }
        ).encode(),
        headers={
            'Referer': f'{base}/timeline/',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    )
    if created != 200 or b'Saved' not in created_body:
        print(f'diaper status={created}')
        return 1
    match = re.search(rb'data-event-id="([0-9a-f-]{36})"', created_body)
    if match is None:
        print('diaper id missing')
        return 1
    event_id = match.group(1).decode('ascii')
    status, timeline = open_url(f'{base}/timeline/')
    token = _csrf(timeline)
    version = _version(timeline, event_id)
    deleted, _body = open_url(
        f'{base}/events/{event_id}/delete/',
        data=urllib.parse.urlencode(
            {'csrfmiddlewaretoken': token, 'version': version}
        ).encode(),
        headers={
            'Referer': f'{base}/timeline/',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    )
    if deleted not in {200, 302}:
        print(f'delete status={deleted}')
        return 1
    print('smoke ok')
    return 0


def _csrf(body: bytes) -> str:
    match = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', body)
    if match is None:
        raise SystemExit('csrf token missing')
    return match.group(1).decode('ascii')


def _version(body: bytes, event_id: str) -> str:
    marker = event_id.encode('ascii')
    chunk = body[body.find(marker) : body.find(marker) + 500]
    match = re.search(rb'name="version" value="(\d+)"', chunk)
    if match is None:
        return '1'
    return match.group(1).decode('ascii')


if __name__ == '__main__':
    raise SystemExit(main())
