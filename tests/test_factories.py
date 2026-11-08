"""Factories emit synthetic identities only."""

from __future__ import annotations

from datetime import timezone
from zoneinfo import ZoneInfo

import pytest

from tests.factories import (
    SYNTHETIC_PASSWORD,
    BabyFactory,
    CaregiverFactory,
    EventFactory,
    HouseholdFactory,
)

pytestmark = pytest.mark.django_db


def test_caregiver_is_a_synthetic_user():
    user = CaregiverFactory()

    assert user.username.startswith('synth_caregiver_')
    assert user.email == f'{user.username}@example.com'
    assert user.first_name
    assert user.last_name
    assert user.password != SYNTHETIC_PASSWORD
    assert user.check_password(SYNTHETIC_PASSWORD)
    assert not user.is_staff
    assert not user.is_superuser


def test_household_baby_and_event_use_fake_names():
    event = EventFactory()

    assert event.kind == 'synthetic'
    assert event.baby.name
    assert event.baby.household.name.endswith(' household')
    assert '\n' not in event.baby.name
    assert '@' not in event.baby.household.name
    ZoneInfo(event.baby.household.timezone)
    assert event.occurred_at.tzinfo is timezone.utc
    assert event.recorded_at == event.occurred_at
    assert event.caregiver.email.endswith('@example.com')


def test_household_and_baby_factories_do_not_need_extra_rows():
    household = HouseholdFactory()
    baby = BabyFactory()

    assert household.name.endswith(' household')
    assert baby.household.name.endswith(' household')
    ZoneInfo(household.timezone)
