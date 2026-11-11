"""Shared setup for product tests. Synthetic names only."""

from __future__ import annotations

from tests.factories import SYNTHETIC_PASSWORD, CaregiverFactory
from tracker.extra_models import AccountControl, Membership
from tracker.models import BabyProfile, Household


def make_user(username=None):
    if username is None:
        return CaregiverFactory()
    return CaregiverFactory(username=username, email=f'{username}@example.com')


def make_household(*, actor=None, timezone='UTC', baby_name='Synth Baby'):
    actor = actor or make_user()
    household = Household(name='Synthetic household', timezone=timezone)
    household.save(actor=actor)
    baby = BabyProfile(household=household, name=baby_name)
    baby.save(actor=actor)
    return household, baby, actor


def add_member(household, role, *, actor, username=None):
    user = make_user(username)
    membership = Membership(
        household=household,
        user=user,
        role=role,
        is_active=True,
    )
    membership.save(actor=actor)
    AccountControl.objects.get_or_create(user=user)
    return user, membership


def login(client, user):
    response = client.post(
        '/login/',
        {
            'username': user.username,
            'password': SYNTHETIC_PASSWORD,
            'next': '/timeline/',
        },
    )
    return response
