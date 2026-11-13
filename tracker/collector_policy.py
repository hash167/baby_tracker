"""Read the collector allowlist out of deploy/otel/collector.yaml."""

from __future__ import annotations

import re
from pathlib import Path

SAFE_KEYS = (
    'http.request.method',
    'http.response.status_code',
    'http.route',
    'db.system',
    'db.operation',
    'error.type',
    'app.event_type',
    'app.outcome',
    'app.role',
)


def allowlist_from_config(text: str) -> list[str]:
    match = re.search(r'keep_keys\(attributes,\s*\[(.*?)\]\)', text, re.DOTALL)
    if match is None:
        raise ValueError('collector config has no attribute allowlist')
    return re.findall(r'"([^"]+)"', match.group(1))


def redact(attributes: dict, allowed: list[str] | tuple[str, ...]) -> dict:
    allowed_set = set(allowed)
    return {key: value for key, value in attributes.items() if key in allowed_set}


def config_text() -> str:
    path = Path(__file__).resolve().parents[1] / 'deploy' / 'otel' / 'collector.yaml'
    return path.read_text()
