"""Backfill of imported fields that an older column name left empty."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from tests.helpers import make_household
from tracker.extra_models import ImportLink
from tracker.models import AuditEntry, Event
from tracker.nara import backfill_import_fields, commit_batch, stage_text

pytestmark = pytest.mark.django_db
FIXTURE = (
    __import__('pathlib').Path(__file__).resolve().parent
    / 'fixtures'
    / 'nara'
    / 'synthetic_real_headers.csv'
)


def _sync(event):
    link = event.import_links.get()
    link.imported_version = event.version
    link.save(update_fields=['imported_version'])


def _commit(actor, household, baby):
    batch = stage_text(
        actor=actor,
        household=household,
        baby=baby,
        text=FIXTURE.read_text(),
        filename='synthetic_real_headers.csv',
    )
    commit_batch(batch, actor=actor, skip_errors=True)
    return batch


def test_backfill_fills_nulls_once_and_skips_edits(tmp_path):
    del tmp_path
    household, baby, actor = make_household()
    batch = _commit(actor, household, baby)
    feed = Event.objects.get(source_activity_id='act-nontimer')
    bottle = Event.objects.get(source_activity_id='act-bottle-milk')
    sleep = Event.objects.get(source_activity_id='act-sleep')
    diaper = Event.objects.get(source_activity_id='act-wet')
    assert feed.left_seconds == 600
    feed.feed_source = Event.FeedSource.IMPORT
    feed.left_seconds = None
    feed.right_seconds = None
    feed.start_side = ''
    feed.end_side = ''
    feed.save(actor=actor, expected_version=feed.version)
    _sync(feed)
    bottle.archive = {'bottle_type': 'breast_milk', 'volume_ml': None}
    bottle.save(actor=actor, expected_version=bottle.version)
    _sync(bottle)
    sleep.archive = {'duration_seconds': '', 'ended_at': ''}
    sleep.save(actor=actor, expected_version=sleep.version)
    _sync(sleep)
    diaper.archive = {'detail': '', 'dirty_color': '', 'dirty_texture': ''}
    diaper.save(actor=actor, expected_version=diaper.version)
    _sync(diaper)

    edited = Event.objects.get(source_activity_id='act-dry')
    edited.notes = 'kept by the caregiver'
    edited.diaper_type = ''
    edited.save(actor=actor, expected_version=edited.version)

    other_household, other_baby, other_actor = make_household()
    _commit(other_actor, other_household, other_baby)
    other_feed = Event.objects.get(
        source_activity_id='act-nontimer', household=other_household
    )
    other_feed.left_seconds = None
    other_feed.save(actor=other_actor, expected_version=other_feed.version)
    _sync(other_feed)

    before_audits = AuditEntry.objects.count()
    preview = backfill_import_fields(dry_run=True, batch_id=batch.pk)
    assert preview['updated'] == 4
    feed.refresh_from_db()
    assert feed.left_seconds is None
    assert AuditEntry.objects.count() == before_audits

    out = StringIO()
    call_command(
        'backfill_import_durations',
        '--batch',
        str(batch.pk),
        stdout=out,
    )
    text = out.getvalue()
    assert 'updated=4' in text
    assert 'Synth' not in text
    assert '600' not in text
    assert 'Blowout' not in text

    feed.refresh_from_db()
    bottle.refresh_from_db()
    sleep.refresh_from_db()
    diaper.refresh_from_db()
    edited.refresh_from_db()
    other_feed.refresh_from_db()
    assert feed.left_seconds == 600
    assert feed.right_seconds == 300
    assert feed.start_side == 'left'
    assert feed.end_side == 'right'
    assert feed.feed_source == 'import'
    assert bottle.archive['volume_ml'] == 90
    assert sleep.archive['duration_seconds'] == '3600'
    assert diaper.archive['detail'] == 'Blowout'
    assert edited.notes == 'kept by the caregiver'
    assert edited.diaper_type == ''
    assert other_feed.left_seconds is None
    assert feed.history().filter(action=AuditEntry.Action.UPDATE).exists()
    link = ImportLink.objects.get(event=feed, batch=batch)
    assert link.imported_version == feed.version

    again = backfill_import_fields(batch_id=batch.pk)
    assert again['updated'] == 0
    feed.refresh_from_db()
    assert feed.left_seconds == 600
