"""Fill imported fields that an older parser left empty.

Prints counts only. Does not print names, notes, or measured values.
"""

from django.core.management.base import BaseCommand

from tracker.nara import backfill_import_fields


class Command(BaseCommand):
    help = 'Fill null imported durations and related blank fields from stored CSV rows.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Count rows that would change without writing.',
        )
        parser.add_argument(
            '--batch',
            default='',
            help='Limit the fill to one import batch id.',
        )

    def handle(self, *args, **options):
        counts = backfill_import_fields(
            dry_run=options['dry_run'],
            batch_id=options['batch'] or None,
        )
        self.stdout.write(
            'examined={examined} updated={updated} '
            'skipped_edited={skipped_edited} unchanged={unchanged} '
            'dry_run={dry_run}'.format(
                dry_run=int(bool(options['dry_run'])),
                **counts,
            )
        )
