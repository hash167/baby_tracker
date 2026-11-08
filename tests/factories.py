"""Synthetic factories for tests.

Names, emails, and events come from Faker and fixed synthetic values.
Do not point these at real exports or personal data.

Household, baby, and event factories persist tracker models. Caregivers
are Django users. `created_by` is the actor stored on the audit row.
"""

from __future__ import annotations

from datetime import datetime, timezone

import factory
from django.contrib.auth import get_user_model

from tracker.models import BabyProfile, Event, Household

SYNTHETIC_PASSWORD = 'synthetic-not-a-real-password'


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


class _ActorFactory(factory.django.DjangoModelFactory):
    """Persist via save(actor=...) so the row is audited."""

    class Meta:
        abstract = True

    created_by = factory.SubFactory(CaregiverFactory)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        actor = kwargs.get('created_by')
        if getattr(actor, 'pk', None) is None:
            raise TypeError('created_by must be a saved user')
        obj = model_class(*args, **kwargs)
        obj.save(actor=actor)
        return obj


class HouseholdFactory(_ActorFactory):
    class Meta:
        model = Household
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


class BabyFactory(_ActorFactory):
    class Meta:
        model = BabyProfile

    name = factory.Faker('first_name')
    household = factory.SubFactory(HouseholdFactory)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventFactory(_ActorFactory):
    class Meta:
        model = Event

    occurred_at = factory.LazyFunction(_utc_now)
    recorded_at = factory.LazyAttribute(lambda obj: obj.occurred_at)
    baby = factory.SubFactory(BabyFactory)
