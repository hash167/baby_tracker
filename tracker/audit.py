"""Field snapshots for audit rows.

Values are JSON-ready primitives. Foreign keys are stored as primary keys,
never as names or emails. This module does not log.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

# Bookkeeping that changes on every successful write and is not a user edit.
# `version` and `updated_by` stay in the snapshot so a history row shows them.
SKIP_SNAPSHOT_FIELDS = frozenset({'updated_at'})


def json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('naive datetime cannot be stored in an audit payload')
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(
        f'Cannot store {type(value).__name__} in an audit payload. '
        'Audit values stay in the database and must be plain JSON.'
    )


def snapshot(instance) -> dict:
    """Serializable column values for `instance`, excluding the primary key."""
    data = {}
    for field in instance._meta.concrete_fields:
        if field.primary_key or field.name in SKIP_SNAPSHOT_FIELDS:
            continue
        data[field.name] = json_value(getattr(instance, field.attname))
    return data


def diff_snapshots(before: dict, after: dict) -> dict:
    """Field-level `{field: {before, after}}` for values that differ."""
    changes = {}
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old != new:
            changes[key] = {'before': old, 'after': new}
    return changes
