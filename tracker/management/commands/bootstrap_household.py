"""Create the first household from environment variables.

The first run requires every value and does not invent a password.
A later run exits 0 when a household or that admin already exists.
It does not change an existing password or any other stored row.
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
        user_model = get_user_model()
        if Household.objects.exists():
            self.stdout.write(
                'Household already exists. The password was left unchanged.'
            )
            return
        username = os.environ.get('BOOTSTRAP_ADMIN_USERNAME', '')
        if username and user_model.objects.filter(username=username).exists():
            self.stdout.write('Admin already exists. The password was left unchanged.')
            return
        missing = [name for name in REQUIRED if not os.environ.get(name)]
        if missing:
            raise CommandError('Missing ' + ', '.join(missing))
        values = {name: os.environ[name] for name in REQUIRED}
        placeholders = [name for name, value in values.items() if 'REPLACE_' in value]
        if placeholders:
            raise CommandError(
                'Replace placeholder bootstrap values before the first run: '
                + ', '.join(placeholders)
            )
        user = user_model.objects.create_user(
            username=values['BOOTSTRAP_ADMIN_USERNAME'],
            email=values['BOOTSTRAP_ADMIN_EMAIL'],
            password=values['BOOTSTRAP_ADMIN_PASSWORD'],
        )
        household = Household(
            name=values['BOOTSTRAP_HOUSEHOLD_NAME'],
            timezone=values['BOOTSTRAP_TIMEZONE'],
        )
        household.save(actor=user)
        baby = BabyProfile(
            household=household,
            name=values['BOOTSTRAP_BABY_NAME'],
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
