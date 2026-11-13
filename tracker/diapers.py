"""Diaper creates and corrections. Notes stay on the row, not in logs."""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from tracker.clock import ensure_aware, reject_if_too_far_future, require_key
from tracker.extra_models import RequestKey
from tracker.models import Event

logger = logging.getLogger(__name__)

DIAPER_TYPES = {choice for choice, _label in Event.DiaperType.choices}

# Text is the label. The mark is a second cue so the type is not color alone.
DIAPER_LABELS = {
    'wet': 'Wet',
    'dirty': 'Dirty',
    'both': 'Wet + dirty',
    'dry': 'Dry',
}
DIAPER_MARKS = {
    'wet': '●',
    'dirty': '■',
    'both': '◆',
    'dry': '○',
}


def create_diaper(
    *,
    actor,
    baby,
    diaper_type: str,
    occurred_at,
    notes: str,
    idempotency_key: str,
    now=None,
):
    key = require_key(idempotency_key)
    if diaper_type not in DIAPER_TYPES:
        raise ValidationError({'diaper_type': 'Choose wet, dirty, both, or dry.'})
    occurred_at = ensure_aware(occurred_at, 'occurred_at')
    reject_if_too_far_future(occurred_at, now=now)
    notes = notes or ''
    if len(notes) > 2000:
        raise ValidationError({'notes': 'Notes are limited to 2000 characters.'})

    existing = (
        RequestKey.objects.filter(actor=actor, scope='diaper', key=key)
        .select_related('event')
        .first()
    )
    if existing and existing.event_id:
        return existing.event, True

    now = now or timezone.now()
    try:
        with transaction.atomic():
            event = Event(
                baby=baby,
                kind=Event.Kind.DIAPER,
                diaper_type=diaper_type,
                occurred_at=occurred_at,
                notes=notes,
                recorded_at=now,
            )
            event.save(actor=actor)
            RequestKey.objects.create(
                actor=actor,
                scope='diaper',
                key=key,
                action='create',
                event=event,
            )
    except IntegrityError:
        existing = RequestKey.objects.get(actor=actor, scope='diaper', key=key)
        return existing.event, True
    return event, False


def log_save_failure() -> None:
    """Count a failed save without the note or any other field value."""
    logger.warning('diaper save failed')
