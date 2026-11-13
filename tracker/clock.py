"""Shared time checks. Instants are timezone-aware UTC."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone


def ensure_aware(value: datetime, field: str) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError({field: 'Enter a time with a timezone.'})
    return value


def reject_if_too_far_future(value: datetime, *, now=None, field='occurred_at') -> None:
    now = now or timezone.now()
    tolerance = timedelta(seconds=settings.EVENT_FUTURE_TOLERANCE_SECONDS)
    if value > now + tolerance:
        raise ValidationError({field: 'That time is too far in the future.'})


def require_key(key: str) -> str:
    cleaned = (key or '').strip()
    if not cleaned or len(cleaned) > 80:
        raise ValidationError({'idempotency_key': 'Missing idempotency key.'})
    return cleaned
