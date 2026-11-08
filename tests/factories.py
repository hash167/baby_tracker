"""Synthetic factories for tests.

Names, emails, and events come from Faker and fixed synthetic values.
Do not point these at real exports or personal data.

Household, baby, and event rows become Django models in issue #11.
Until then those three factories build in-memory records. Caregivers are
Django users.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import factory
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser

SYNTHETIC_PASSWORD = 'synthetic-not-a-real-password'


@dataclass
class SyntheticHousehold:
    name: str
    timezone: str


@dataclass
class SyntheticBaby:
    name: str
    household: SyntheticHousehold


@dataclass
class SyntheticEvent:
    kind: str
    occurred_at: datetime
    recorded_at: datetime
    baby: SyntheticBaby
    caregiver: AbstractBaseUser


class CaregiverFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()

    username = factory.Sequence(lambda n: f'synth_caregiver_{n}')
    first_name = factory.Faker('first_name')
    last_name = factory.Faker('last_name')
    email = factory.LazyAttribute(lambda obj: f'{obj.username}@example.com')
    password = factory.django.Password(SYNTHETIC_PASSWORD)
    is_staff = False
    is_superuser = False


class HouseholdFactory(factory.Factory):
    class Meta:
        model = SyntheticHousehold
        exclude = ('family_name',)

    family_name = factory.Faker('last_name')
    name = factory.LazyAttribute(lambda obj: f'{obj.family_name} household')
    timezone = factory.Iterator(
        [
            'America/Los_Angeles',
            'America/New_York',
            'UTC',
        ]
    )


class BabyFactory(factory.Factory):
    class Meta:
        model = SyntheticBaby

    name = factory.Faker('first_name')
    household = factory.SubFactory(HouseholdFactory)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventFactory(factory.Factory):
    class Meta:
        model = SyntheticEvent

    kind = 'synthetic'
    occurred_at = factory.LazyFunction(_utc_now)
    recorded_at = factory.LazyAttribute(lambda obj: obj.occurred_at)
    baby = factory.SubFactory(BabyFactory)
    caregiver = factory.SubFactory(CaregiverFactory)
