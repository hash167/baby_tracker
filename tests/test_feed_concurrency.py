"""Two PostgreSQL connections start one feed."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from django.db import connection

from tests.helpers import make_household
from tracker.feeds import apply_timer
from tracker.models import Event

pytestmark = pytest.mark.django_db(transaction=True)
UTC = timezone.utc


def test_two_simultaneous_starts_create_one_session():
    _household, baby, actor = make_household()
    barrier = threading.Barrier(2)
    found = []
    errors = []

    def worker(key):
        connection.close()
        try:
            barrier.wait(timeout=5)
            session, _replay = apply_timer(
                actor=actor,
                baby=baby,
                action='start',
                side='left',
                idempotency_key=key,
                expected_version=None,
                now=datetime(2026, 6, 15, 12, tzinfo=UTC),
            )
            found.append(session.pk)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=worker, args=(f'start-{index}',)) for index in (1, 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    assert len(found) == 2
    assert found[0] == found[1]
    assert (
        Event.objects.filter(
            baby=baby,
            kind=Event.Kind.FEED,
            feed_state__in=['active', 'paused'],
        ).count()
        == 1
    )
