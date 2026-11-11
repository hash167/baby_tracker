from django.core.management.base import BaseCommand

from tracker.nara import purge_expired


class Command(BaseCommand):
    help = 'Delete stored import files whose retention period has ended.'

    def handle(self, *args, **options):
        removed = purge_expired()
        self.stdout.write(f'purged {removed}')
