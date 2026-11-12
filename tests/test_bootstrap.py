"""First-household command. Synthetic values only."""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.factories import SYNTHETIC_PASSWORD, CaregiverFactory
from tests.helpers import make_household
from tracker.extra_models import Membership, Role
from tracker.models import BabyProfile, Household

pytestmark = pytest.mark.django_db

ENV = {
    'BOOTSTRAP_ADMIN_USERNAME': 'synth_bootstrap',
    'BOOTSTRAP_ADMIN_PASSWORD': 'synthetic-bootstrap-password',  # nosec B105
    'BOOTSTRAP_ADMIN_EMAIL': 'synth_bootstrap@example.com',
    'BOOTSTRAP_HOUSEHOLD_NAME': 'Synthetic household',
    'BOOTSTRAP_BABY_NAME': 'Synth Baby',
    'BOOTSTRAP_TIMEZONE': 'America/Los_Angeles',
}


def _set_env(monkeypatch, **overrides):
    values = dict(ENV)
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_bootstrap_requires_every_value(monkeypatch):
    _clear_env(monkeypatch)
    with pytest.raises(CommandError, match='Missing'):
        call_command('bootstrap_household')


def test_bootstrap_rejects_placeholders(monkeypatch):
    _set_env(monkeypatch, BOOTSTRAP_ADMIN_USERNAME='REPLACE_USERNAME')
    with pytest.raises(CommandError, match='placeholder'):
        call_command('bootstrap_household')
    assert not get_user_model().objects.filter(username='REPLACE_USERNAME').exists()


def test_bootstrap_creates_admin_household_and_baby(monkeypatch):
    _set_env(monkeypatch)
    out = StringIO()
    call_command('bootstrap_household', stdout=out)
    assert 'household created' in out.getvalue()
    user = get_user_model().objects.get(username='synth_bootstrap')
    assert user.check_password('synthetic-bootstrap-password')  # nosec B105
    household = Household.objects.get()
    assert household.name == 'Synthetic household'
    assert household.timezone == 'America/Los_Angeles'
    baby = BabyProfile.objects.get(household=household)
    assert baby.name == 'Synth Baby'
    membership = Membership.objects.get(user=user, household=household)
    assert membership.role == Role.ADMIN
    assert membership.is_active is True


def test_second_run_leaves_the_password(monkeypatch):
    _set_env(monkeypatch)
    call_command('bootstrap_household')
    user = get_user_model().objects.get(username='synth_bootstrap')
    _set_env(
        monkeypatch,
        BOOTSTRAP_ADMIN_PASSWORD='different-synthetic-password',  # nosec B106
    )
    out = StringIO()
    call_command('bootstrap_household', stdout=out)
    assert 'password was left unchanged' in out.getvalue()
    user.refresh_from_db()
    assert user.check_password('synthetic-bootstrap-password')  # nosec B105
    assert not user.check_password('different-synthetic-password')  # nosec B105
    assert Household.objects.count() == 1
    assert get_user_model().objects.filter(username='synth_bootstrap').count() == 1


def test_existing_household_needs_no_password(monkeypatch):
    make_household()
    _clear_env(monkeypatch)
    out = StringIO()
    call_command('bootstrap_household', stdout=out)
    assert 'Household already exists' in out.getvalue()
    assert 'password was left unchanged' in out.getvalue()
    assert Household.objects.count() == 1


def test_existing_admin_is_not_rewritten(monkeypatch):
    user = CaregiverFactory(username='synth_bootstrap', email='kept@example.com')
    assert user.check_password(SYNTHETIC_PASSWORD)
    _set_env(monkeypatch)
    out = StringIO()
    call_command('bootstrap_household', stdout=out)
    assert 'Admin already exists' in out.getvalue()
    user.refresh_from_db()
    assert user.email == 'kept@example.com'
    assert user.check_password(SYNTHETIC_PASSWORD)
    assert not user.check_password('synthetic-bootstrap-password')  # nosec B105
    assert Household.objects.count() == 0
