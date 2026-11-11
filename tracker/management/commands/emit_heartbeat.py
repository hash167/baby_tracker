"""Emit one heartbeat span. A missing collector does not fail the command."""

from django.core.management.base import BaseCommand

from config.telemetry import record_functional_attributes


class Command(BaseCommand):
    help = 'Record a heartbeat span with no personal data.'

    def handle(self, *args, **options):
        record_functional_attributes(event_type='heartbeat', outcome='success')
        self.stdout.write('heartbeat')
