"""Playwright seed keeps a diaper outside 24h and inside 7 days."""

from __future__ import annotations

import pytest
from django.core.management import call_command

from tracker.models import BabyProfile, Event
from tracker.summary import events_in_range

pytestmark = pytest.mark.django_db


def test_seed_diaper_makes_seven_days_larger_than_one_day():
    call_command('seed_e2e')
    call_command('seed_e2e')
    baby = BabyProfile.objects.get(name='Synth Baby')
    day = [
        event
        for event in events_in_range(baby, '24h')
        if event.kind == Event.Kind.DIAPER
    ]
    week = [
        event
        for event in events_in_range(baby, '7d')
        if event.kind == Event.Kind.DIAPER
    ]
    assert len(week) >= len(day) + 1
    assert (
        Event.objects.filter(source_activity_id='range-diaper-outside-24h').count() == 1
    )
