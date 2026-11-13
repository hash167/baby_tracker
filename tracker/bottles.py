"""Manual bottle feeds. Volumes stay on the row, not in logs or telemetry."""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from tracker.clock import ensure_aware, reject_if_too_far_future, require_key
from tracker.extra_models import RequestKey
from tracker.models import Event
from tracker.nara import ML_PER_FLOZ

logger = logging.getLogger(__name__)

BOTTLE_TYPES = {
    'breast_milk': 'Breast milk',
    'formula': 'Formula',
    'mixed': 'Breast milk + formula',
}
UNITS = {'ml': 'ml', 'floz': 'floz'}


def create_bottle(
    *,
    actor,
    baby,
    bottle_type: str,
    volume: str,
    unit: str,
    occurred_at,
    idempotency_key: str,
    now=None,
):
    key = require_key(idempotency_key)
    if bottle_type not in BOTTLE_TYPES:
        raise ValidationError({'bottle_type': 'Choose breast milk, formula, or mixed.'})
    occurred_at = ensure_aware(occurred_at, 'occurred_at')
    reject_if_too_far_future(occurred_at, now=now)
    measurement = _volume(volume, unit)
    existing = (
        RequestKey.objects.filter(actor=actor, scope='bottle', key=key)
        .select_related('event')
        .first()
    )
    if existing and existing.event_id:
        return existing.event, True
    now = now or timezone.now()
    archive = {
        'bottle_type': bottle_type,
        'bottle_type_raw': '',
        'formula_name': '',
        'volume': measurement,
        'breast_milk': None,
        'formula': None,
        'volume_ml': None if measurement is None else measurement.get('ml'),
    }
    try:
        with transaction.atomic():
            event = Event(
                baby=baby,
                kind=Event.Kind.BOTTLE,
                occurred_at=occurred_at,
                recorded_at=now,
                archive=archive,
            )
            event.save(actor=actor)
            RequestKey.objects.create(
                actor=actor,
                scope='bottle',
                key=key,
                action='create',
                event=event,
            )
    except IntegrityError:
        existing = RequestKey.objects.get(actor=actor, scope='bottle', key=key)
        return existing.event, True
    return event, False


def _volume(volume: str, unit: str):
    text = (volume or '').strip()
    if text == '':
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise ValidationError({'volume': 'Enter a volume or leave it blank.'}) from exc
    if number < 0:
        raise ValidationError({'volume': 'Volume cannot be negative.'})
    normalized = UNITS.get((unit or '').strip().lower())
    if normalized is None:
        raise ValidationError({'unit': 'Choose ml or fl oz.'})
    ml = number if normalized == 'ml' else number * ML_PER_FLOZ
    return {'amount': number, 'unit': normalized, 'ml': ml}


def log_save_failure() -> None:
    logger.warning('bottle save failed')
