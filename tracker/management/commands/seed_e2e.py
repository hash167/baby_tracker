"""Synthetic accounts for Playwright. Safe to run more than once."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from tests.factories import SYNTHETIC_PASSWORD
from tracker.extra_models import AccountControl, Membership, Role
from tracker.models import BabyProfile, Household

USERS = (
    ('synth_admin', 'synth_admin@example.com', Role.ADMIN),
    ('synth_parent', 'synth_parent@example.com', Role.PARENT),
    ('synth_caregiver', 'synth_caregiver@example.com', Role.CAREGIVER),
)


class Command(BaseCommand):
    help = 'Create the synthetic household used by browser tests.'

    def handle(self, *args, **options):
        user_model = get_user_model()
        admin = user_model.objects.filter(username='synth_admin').first()
        if admin is None:
            admin = user_model.objects.create_user(
                username='synth_admin',
                email='synth_admin@example.com',
                password=SYNTHETIC_PASSWORD,
            )
        else:
            admin.set_password(SYNTHETIC_PASSWORD)
            admin.save(update_fields=['password'])
        household = Household.objects.filter(name='Synthetic Household').first()
        if household is None:
            household = Household(name='Synthetic Household', timezone='UTC')
            household.save(actor=admin)
        elif household.timezone != 'UTC':
            household.timezone = 'UTC'
            household.save(actor=admin, expected_version=household.version)
        baby = BabyProfile.objects.filter(
            household=household,
            name='Synth Baby',
        ).first()
        if baby is None:
            baby = BabyProfile(household=household, name='Synth Baby')
            baby.save(actor=admin)
        for username, email, role in USERS:
            user = user_model.objects.filter(username=username).first()
            if user is None:
                user = user_model.objects.create_user(
                    username=username,
                    email=email,
                    password=SYNTHETIC_PASSWORD,
                )
            else:
                user.email = email
                user.set_password(SYNTHETIC_PASSWORD)
                user.save(update_fields=['email', 'password'])
            AccountControl.objects.get_or_create(user=user)
            membership = Membership.objects.filter(
                household=household,
                user=user,
            ).first()
            if membership is None:
                membership = Membership(
                    household=household,
                    user=user,
                    role=role,
                    is_active=True,
                )
                membership.save(actor=admin)
            elif membership.role != role or not membership.is_active:
                membership.role = role
                membership.is_active = True
                membership.save(actor=admin, expected_version=membership.version)
        self.stdout.write('seeded synthetic household')
