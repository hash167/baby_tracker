"""Create the first household from environment variables.

Refuses to run when any value is missing. Does not invent a password.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tracker.extra_models import AccountControl, Membership, Role
from tracker.models import BabyProfile, Household

REQUIRED = (
    'BOOTSTRAP_ADMIN_USERNAME',
    'BOOTSTRAP_ADMIN_PASSWORD',
    'BOOTSTRAP_ADMIN_EMAIL',
    'BOOTSTRAP_HOUSEHOLD_NAME',
    'BOOTSTRAP_BABY_NAME',
    'BOOTSTRAP_TIMEZONE',
)


class Command(BaseCommand):
    help = 'Create the first admin, household, and baby from the environment.'

    def handle(self, *args, **options):
        missing = [name for name in REQUIRED if not os.environ.get(name)]
        if missing:
            raise CommandError('Missing ' + ', '.join(missing))
        user_model = get_user_model()
        username = os.environ['BOOTSTRAP_ADMIN_USERNAME']
        if user_model.objects.filter(username=username).exists():
            raise CommandError('That username already exists.')
        user = user_model.objects.create_user(
            username=username,
            email=os.environ['BOOTSTRAP_ADMIN_EMAIL'],
            password=os.environ['BOOTSTRAP_ADMIN_PASSWORD'],
        )
        household = Household(
            name=os.environ['BOOTSTRAP_HOUSEHOLD_NAME'],
            timezone=os.environ['BOOTSTRAP_TIMEZONE'],
        )
        household.save(actor=user)
        baby = BabyProfile(
            household=household,
            name=os.environ['BOOTSTRAP_BABY_NAME'],
        )
        baby.save(actor=user)
        membership = Membership(
            household=household,
            user=user,
            role=Role.ADMIN,
            is_active=True,
        )
        membership.save(actor=user)
        AccountControl.objects.get_or_create(user=user)
        self.stdout.write('household created')
