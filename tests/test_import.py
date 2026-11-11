"""Nara CSV staging, validation, dedup, commit, and rollback."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from tests.helpers import add_member, login, make_household
from tracker.extra_models import FeedingSegment, ImportRow, Role
from tracker.models import Event
from tracker.nara import (
    RollbackNeedsDecision,
    commit_batch,
    parse_csv,
    purge_expired,
    rollback_batch,
    stage_text,
    stage_upload,
)

pytestmark = pytest.mark.django_db
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'nara' / 'synthetic_basic.csv'
NOTE = 'synthetic note'


def _text(**replacements) -> str:
    text = FIXTURE.read_text()
    for old, new in replacements.items():
        text = text.replace(old, new, 1)
    return text


def test_parser_flags_mismatch_enum_units_and_dst(caplog):
    import csv
    import io

    header = [
        'Type',
        'Start Date/time',
        'Start Date/time (Epoch)',
        'Time Zone',
        '_activityKey',
        '[Diaper] Status',
        '[Breastfeed] Left Duration',
        '[Breastfeed] Right Duration',
        '[Breastfeed] Duration Unit',
        '[Temperature] Value',
        '[Temperature] Unit',
        'Note',
    ]
    table = [
        [
            'Diaper',
            '2026-06-15 15:00:00',
            '1781532000000',
            'UTC',
            'act-mismatch',
            'Wet',
            '',
            '',
            '',
            '',
            '',
            '',
        ],
        [
            'Diaper',
            '2026-06-15 15:05:00',
            '1781535900000',
            'UTC',
            'act-enum',
            'Sparkly',
            '',
            '',
            '',
            '',
            '',
            '',
        ],
        [
            'Breastfeed',
            '2026-06-15 16:00:00',
            '1781539200000',
            'UTC',
            'act-missing',
            '',
            '',
            '',
            's',
            '',
            '',
            '',
        ],
        [
            'Breastfeed',
            '2026-06-15 16:05:00',
            '1781539500000',
            'UTC',
            'act-zero',
            '',
            '0',
            '0',
            's',
            '',
            '',
            '',
        ],
        [
            'Breastfeed',
            '2026-06-15 16:10:00',
            '1781539800000',
            'UTC',
            'act-nounit',
            '',
            '5',
            '0',
            '',
            '',
            '',
            '',
        ],
        [
            'Medical',
            '2026-11-01 01:30:00',
            '1761978600000',
            'America/New_York',
            'act-dst',
            '',
            '',
            '',
            '',
            '98.6',
            'C',
            '',
        ],
        [
            'Weird',
            '2026-06-15 17:00:00',
            '1781542800000',
            'UTC',
            'act-weird',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
        ],
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(header)
    writer.writerows(table)
    mismatch = buffer.getvalue()
    # A broken quote is reported by row number only.
    broken = mismatch + 'Diaper,"unterminated,UTC,act-bad,Wet\n'
    with caplog.at_level('WARNING'):
        rows = parse_csv(__import__('io').StringIO(broken))
    by_id = {row.activity_id: row for row in rows if row.activity_id}
    assert any(
        item['code'] == 'epoch_mismatch' for item in by_id['act-mismatch'].warnings
    )
    mismatch_warning = next(
        item
        for item in by_id['act-mismatch'].warnings
        if item['code'] == 'epoch_mismatch'
    )
    assert 'readable' in mismatch_warning and 'epoch' in mismatch_warning
    assert any(item['code'] == 'unknown_enum' for item in by_id['act-enum'].warnings)
    assert by_id['act-enum'].mapped.get('diaper_type') == ''
    assert by_id['act-missing'].mapped['left_seconds'] is None
    assert by_id['act-zero'].mapped['left_seconds'] == 0
    assert by_id['act-zero'].mapped['right_seconds'] == 0
    assert any(
        item['code'] == 'missing_or_unknown_unit'
        for item in by_id['act-nounit'].warnings
    )
    assert by_id['act-nounit'].mapped['left_seconds'] is None
    assert any(item['code'] == 'ambiguous_time' for item in by_id['act-dst'].warnings)
    assert any(item['code'] == 'implausible_unit' for item in by_id['act-dst'].warnings)
    assert by_id['act-weird'].mapped['kind'] == 'unknown'
    malformed = [
        row for row in rows if row.classification == 'error' and not row.activity_id
    ]
    assert malformed
    assert NOTE not in caplog.text
    assert 'unterminated' not in caplog.text


def test_stage_commit_dedup_and_rollback(client):
    household, baby, admin = make_household()
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_importer'
    )
    caregiver, _care = add_member(
        household, Role.CAREGIVER, actor=admin, username='synth_no_import'
    )
    users_before = get_user_model().objects.count()
    batch = stage_text(
        actor=parent,
        household=household,
        baby=baby,
        text=FIXTURE.read_text(),
        filename='synthetic_basic.csv',
    )
    assert Event.objects.count() == 0
    assert batch.rows.exclude(record_type='').exists()
    login(client, caregiver)
    assert client.get('/import/').status_code == 403
    assert client.get(f'/import/{batch.pk}/').status_code == 403

    commit_batch(batch, actor=parent, skip_errors=True)
    assert Event.objects.filter(
        kind=Event.Kind.FEED, source_activity_id='act-feed-1'
    ).exists()
    feed = Event.objects.get(source_activity_id='act-feed-1')
    assert feed.feed_source == 'import'
    assert feed.left_seconds == 600
    assert feed.right_seconds == 300
    assert feed.segments.count() == 0
    assert FeedingSegment.objects.count() == 0
    assert Event.objects.filter(kind=Event.Kind.SLEEP).exists()
    assert Event.objects.filter(kind=Event.Kind.GROWTH).exists()
    assert Event.objects.filter(kind=Event.Kind.MEDICAL).exists()
    unknown = Event.objects.get(source_activity_id='act-unknown-diaper')
    assert unknown.kind == Event.Kind.UNKNOWN
    assert unknown.diaper_type == ''
    zero = Event.objects.get(source_activity_id='act-feed-zero')
    assert zero.left_seconds == 0
    assert zero.right_seconds is None
    assert get_user_model().objects.count() == users_before
    assert feed.attributed_name == 'Caregiver One'
    assert feed.attributed_to_id is None

    again = stage_text(
        actor=parent,
        household=household,
        baby=baby,
        text=FIXTURE.read_text(),
    )
    assert again.rows.filter(disposition='already').exists()
    before = Event.objects.count()
    commit_batch(again, actor=parent, skip_errors=True)
    assert Event.objects.count() == before

    edited = Event.objects.get(source_activity_id='act-diaper-1')
    edited.notes = 'local edit'
    edited.save(actor=parent, expected_version=edited.version)
    same = stage_text(
        actor=parent, household=household, baby=baby, text=FIXTURE.read_text()
    )
    diaper_row = same.rows.get(source_activity_id='act-diaper-1')
    assert diaper_row.disposition == 'already'
    commit_batch(same, actor=parent, skip_errors=True)
    edited.refresh_from_db()
    assert edited.notes == 'local edit'

    changed_text = FIXTURE.read_text().replace(
        'synthetic note', 'synthetic note changed'
    )
    changed = stage_text(
        actor=parent, household=household, baby=baby, text=changed_text
    )
    changed_row = changed.rows.get(source_activity_id='act-diaper-1')
    assert changed_row.disposition == 'changed'
    commit_batch(changed, actor=parent, skip_errors=True)
    edited.refresh_from_db()
    assert edited.notes == 'local edit'
    changed_row.decision = 'apply'
    changed_row.save(update_fields=['decision'])
    # The batch is already committed; a new batch applies the change.
    follow = stage_text(actor=parent, household=household, baby=baby, text=changed_text)
    follow_row = follow.rows.get(source_activity_id='act-diaper-1')
    follow_row.decision = 'apply'
    follow_row.save(update_fields=['decision'])
    commit_batch(follow, actor=parent, skip_errors=True)
    edited.refresh_from_db()
    assert edited.notes == 'synthetic note changed'

    from tracker.diapers import create_diaper

    manual_event, _replay = create_diaper(
        actor=parent,
        baby=baby,
        diaper_type='dry',
        occurred_at=timezone.now(),
        notes='manual',
        idempotency_key='manual-beside-import',
    )
    with pytest.raises(RollbackNeedsDecision) as caught:
        rollback_batch(batch, actor=parent)
    decisions = {str(event.pk): 'remove' for event in caught.value.events}
    rolled = rollback_batch(batch, actor=parent, decisions=decisions)
    assert rolled.status == 'rolled_back'
    assert not Event.objects.filter(source_activity_id='act-feed-1').exists()
    assert Event.objects.filter(pk=manual_event.pk).exists()

    edited.refresh_from_db()
    if edited.version:
        try:
            rollback_batch(follow, actor=parent)
        except RollbackNeedsDecision as exc:
            assert exc.events
            rollback_batch(
                follow,
                actor=parent,
                decisions={str(edited.pk): 'keep'},
            )
            edited.refresh_from_db()
            assert edited.notes == 'synthetic note changed'

    restored = stage_text(
        actor=parent, household=household, baby=baby, text=FIXTURE.read_text()
    )
    assert restored.rows.filter(disposition='reimport').exists()
    commit_batch(restored, actor=parent, skip_errors=True)
    assert Event.objects.filter(source_activity_id='act-feed-1').count() == 1
    assert Event.all_objects.filter(source_activity_id='act-feed-1').count() == 1


def test_commit_failure_leaves_no_rows_and_files_expire(monkeypatch):
    household, baby, actor = make_household()
    batch = stage_text(
        actor=actor,
        household=household,
        baby=baby,
        text=FIXTURE.read_text(),
    )
    calls = {'n': 0}
    original = Event.save

    def boom(self, *args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('commit failed')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Event, 'save', boom)
    with pytest.raises(RuntimeError):
        commit_batch(batch, actor=actor, skip_errors=True)
    assert Event.objects.count() == 0
    batch.refresh_from_db()
    assert batch.status == 'staged'

    upload = SimpleUploadedFile(
        'synthetic.csv', FIXTURE.read_bytes(), content_type='text/csv'
    )
    stored = stage_upload(
        actor=actor,
        household=household,
        baby=baby,
        uploaded=upload,
        text=FIXTURE.read_text(),
    )
    stored.purge_after = timezone.now() - timedelta(days=1)
    stored.save(actor=actor, expected_version=stored.version)
    assert purge_expired() == 1
    stored.refresh_from_db()
    assert stored.stored_name == ''
    assert stored.purged_at is not None
    assert ImportRow.objects.filter(batch=stored).exists()
