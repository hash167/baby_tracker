"""Short pump and sleep logs. Amounts and notes stay off telemetry."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from tracker.clock import ensure_aware, reject_if_too_far_future, require_key
from tracker.extra_models import RequestKey
from tracker.models import Event

logger = logging.getLogger(__name__)


def create_pump(
    *,
    actor,
    baby,
    volume: str,
    occurred_at,
    idempotency_key: str,
    now=None,
):
    key = require_key(idempotency_key)
    occurred_at = ensure_aware(occurred_at, 'occurred_at')
    reject_if_too_far_future(occurred_at, now=now)
    amount = _ml(volume)
    archive = {
        'volume_ml': amount,
        'total': None
        if amount is None
        else {'amount': amount, 'unit': 'ml', 'ml': amount},
    }
    return _once(
        actor,
        baby,
        key,
        'pump',
        Event.Kind.PUMP,
        occurred_at,
        archive,
        now,
    )


def create_sleep(
    *,
    actor,
    baby,
    occurred_at,
    ended_at,
    idempotency_key: str,
    notes: str = '',
    now=None,
):
    key = require_key(idempotency_key)
    occurred_at = ensure_aware(occurred_at, 'occurred_at')
    reject_if_too_far_future(occurred_at, now=now)
    notes = notes or ''
    if len(notes) > 2000:
        raise ValidationError({'notes': 'Notes are limited to 2000 characters.'})
    archive = {'duration_seconds': '', 'ended_at': ''}
    if ended_at is not None:
        ended_at = ensure_aware(ended_at, 'ended_at')
        if ended_at < occurred_at:
            raise ValidationError({'ended_at': 'End is before the start.'})
        archive = {
            'duration_seconds': int((ended_at - occurred_at).total_seconds()),
            'ended_at': ended_at.isoformat(),
        }
    event, replay = _once(
        actor,
        baby,
        key,
        'sleep',
        Event.Kind.SLEEP,
        occurred_at,
        archive,
        now,
    )
    if not replay and notes:
        event.notes = notes
        event.save(actor=actor, expected_version=event.version)
    return event, replay


def _ml(volume: str):
    text = (volume or '').strip()
    if text == '':
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise ValidationError({'volume': 'Enter a volume or leave it blank.'}) from exc
    if number < 0:
        raise ValidationError({'volume': 'Volume cannot be negative.'})
    return number


def _once(actor, baby, key, scope, kind, occurred_at, archive, now):
    existing = (
        RequestKey.objects.filter(actor=actor, scope=scope, key=key)
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
                kind=kind,
                occurred_at=occurred_at,
                recorded_at=now,
                archive=archive,
            )
            event.save(actor=actor)
            RequestKey.objects.create(
                actor=actor,
                scope=scope,
                key=key,
                action='create',
                event=event,
            )
    except IntegrityError:
        existing = RequestKey.objects.get(actor=actor, scope=scope, key=key)
        return existing.event, True
    return event, False


def log_save_failure(kind: str) -> None:
    logger.warning('%s save failed', kind)


def end_after(start, minutes: str):
    text = (minutes or '').strip()
    if text == '':
        return None
    try:
        count = float(text)
    except ValueError as exc:
        raise ValidationError(
            {'minutes': 'Enter minutes or leave the end blank.'}
        ) from exc
    if count < 0:
        raise ValidationError({'minutes': 'Minutes cannot be negative.'})
    return start + timedelta(minutes=count)
