"""Synthetic rows that use the column names and spellings from a real Nara export.

The real file stays off this repository.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client

from tests.helpers import add_member, login, make_household
from tracker.extra_models import Role
from tracker.models import Event
from tracker.nara import commit_batch, parse_csv, stage_text
from tracker.summary import events_in_range, range_summary

pytestmark = pytest.mark.django_db
FIXTURE = (
    Path(__file__).resolve().parent / 'fixtures' / 'nara' / 'synthetic_real_headers.csv'
)


def _by_id(text=None):
    rows = parse_csv((text or FIXTURE.read_text()).splitlines())
    return {row.activity_id: row for row in rows if row.activity_id}


def _codes(row):
    return [item['code'] for item in row.warnings]


def test_real_headers_map_without_spurious_warnings():
    rows = _by_id()
    profile = next(
        row
        for row in parse_csv(FIXTURE.read_text().splitlines())
        if row.record_type == 'Profile'
    )
    assert profile.warnings == []
    assert profile.mapped['profile_type'] == 'child'

    wet = rows['act-wet']
    assert _codes(wet) == []
    assert wet.mapped['diaper_type'] == 'wet'
    assert wet.mapped['archive']['detail'] == 'Blowout'
    assert wet.mapped['archive']['dirty_color'] == 'BROWN'

    assert rows['act-dirty-wet'].mapped['diaper_type'] == 'both'
    assert _codes(rows['act-dirty-wet']) == []
    assert rows['act-dirty'].mapped['diaper_type'] == 'dirty'
    assert rows['act-dirty'].mapped['archive']['dirty_color'] == 'YELLOW'
    assert rows['act-dirty'].mapped['archive']['dirty_texture'] == 'MUSH'
    assert rows['act-green'].mapped['archive']['dirty_color'] == 'GREEN'
    assert rows['act-black'].mapped['archive']['dirty_color'] == 'BLACK'
    assert rows['act-dry'].mapped['diaper_type'] == 'dry'
    assert rows['act-status'].mapped['diaper_type'] == 'wet'
    assert _codes(rows['act-status']) == []

    feed = rows['act-nontimer']
    assert _codes(feed) == []
    assert feed.mapped['start_side'] == 'left'
    assert feed.mapped['end_side'] == 'right'
    assert feed.mapped['manual_entry'] is True
    assert feed.mapped['kind'] == 'feed'

    milk = rows['act-bottle-milk']
    assert _codes(milk) == []
    assert milk.mapped['kind'] == 'bottle'
    assert milk.mapped['archive']['bottle_type'] == 'breast_milk'
    assert milk.mapped['archive']['volume_ml'] == 90

    formula = rows['act-bottle-formula']
    assert _codes(formula) == []
    assert formula.mapped['archive']['bottle_type'] == 'formula'
    assert formula.mapped['archive']['formula_name'] == 'Synthetic Formula'
    assert formula.mapped['archive']['volume']['unit'] == 'FLOZ'

    mixed = rows['act-bottle-mixed']
    assert _codes(mixed) == []
    assert mixed.mapped['archive']['bottle_type'] == 'mixed'
    assert mixed.mapped['archive']['volume_ml'] == 90

    empty = rows['act-bottle-empty']
    assert _codes(empty) == []
    assert empty.mapped['archive']['volume_ml'] is None

    nounit = rows['act-bottle-nounit']
    assert 'missing_or_unknown_unit' in _codes(nounit)
    assert nounit.mapped['archive']['volume_ml'] is None

    pump = rows['act-pump']
    assert _codes(pump) == []
    assert pump.mapped['kind'] == 'pump'
    assert pump.mapped['archive']['volume_ml'] == 120
    assert pump.mapped['archive']['duration_seconds'] == '600'
    assert pump.mapped['ended_at']

    assert _codes(rows['act-pump-empty']) == []
    assert rows['act-pump-empty'].mapped['archive']['volume_ml'] is None

    sleep = rows['act-sleep']
    assert _codes(sleep) == []
    assert sleep.mapped['ended_at']
    assert sleep.mapped['archive']['duration_seconds'] == '3600'

    growth = rows['act-growth']
    assert _codes(growth) == []
    assert growth.mapped['archive']['head'] == '36'
    assert growth.mapped['archive']['head_unit'] == 'cm'

    medical = rows['act-med']
    assert _codes(medical) == []
    assert medical.mapped['archive']['name'] == 'Vitamin'
    assert medical.mapped['archive']['temperature'] == '37'


def test_commit_shows_labels_counts_and_reimport_is_idle():
    household, baby, admin = make_household()
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_nara_parent'
    )
    batch = stage_text(
        actor=parent,
        household=household,
        baby=baby,
        text=FIXTURE.read_text(),
        filename='synthetic_real_headers.csv',
    )
    commit_batch(batch, actor=parent, skip_errors=True)
    bottle = Event.objects.get(source_activity_id='act-bottle-milk')
    pump = Event.objects.get(source_activity_id='act-pump')
    feed = Event.objects.get(source_activity_id='act-nontimer')
    diaper = Event.objects.get(source_activity_id='act-dirty-wet')
    assert bottle.kind == Event.Kind.BOTTLE
    assert pump.kind == Event.Kind.PUMP
    assert feed.feed_source == 'manual'
    assert diaper.diaper_type == 'both'
    wet = Event.objects.get(source_activity_id='act-wet')
    assert wet.archive['detail'] == 'Blowout'

    # The fixture instants are in 2026, so count them with an explicit now.
    counted_at = wet.occurred_at + timedelta(hours=5)
    summary = range_summary(events_in_range(baby, '24h', now=counted_at))
    assert summary['bottles']['total'] >= 1
    assert summary['pumps']['total'] == 2
    assert summary['pumps']['volume_ml'] == 120
    assert summary['diapers']['both'] == 1
    milk_only = [
        event
        for event in events_in_range(baby, '7d', now=counted_at)
        if event.source_activity_id == 'act-bottle-milk'
    ]
    assert len(milk_only) == 1
    assert range_summary(milk_only)['bottles'] == {'total': 1, 'volume_ml': 90.0}

    page = Client()
    login(page, parent)
    viewed = page.get(f'/timeline/{wet.occurred_at.date().isoformat()}/')
    assert viewed.status_code == 200
    body = viewed.content.decode()
    assert 'Bottle · Breast milk · 90 ml' in body
    assert 'Pump · 120 ml' in body
    assert 'Bottle · Breast milk + formula · 90 ml' in body
    assert 'Bottle · Formula · 3 fl oz' in body

    again = stage_text(
        actor=parent,
        household=household,
        baby=baby,
        text=FIXTURE.read_text(),
    )
    assert again.rows.filter(
        source_activity_id='act-bottle-milk'
    ).get().disposition == ('already')
    assert (
        again.rows.filter(source_activity_id='act-pump').get().disposition == 'already'
    )
    before = Event.objects.count()
    commit_batch(again, actor=parent, skip_errors=True)
    assert Event.objects.count() == before
    assert Event.objects.filter(source_activity_id='act-bottle-milk').count() == 1


def test_manual_bottle_is_labeled_and_counted(client):
    household, baby, admin = make_household()
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_bottle_parent'
    )
    login(client, parent)
    saved = client.post(
        '/bottles/',
        {
            'bottle_type': 'breast_milk',
            'volume': '90',
            'unit': 'ml',
            'idempotency_key': 'bottle-key-1',
        },
    )
    assert saved.status_code == 200
    again = client.post(
        '/bottles/',
        {
            'bottle_type': 'formula',
            'volume': '10',
            'unit': 'ml',
            'idempotency_key': 'bottle-key-1',
        },
    )
    assert again.status_code == 200
    assert Event.objects.filter(kind=Event.Kind.BOTTLE).count() == 1
    page = client.get('/timeline/')
    assert page.status_code == 200
    body = page.content.decode()
    assert 'Bottle · Breast milk · 90 ml' in body
    assert 'id="bottle-total">1<' in body
