"""Diaper type is a primary label on the timeline, history, and edit views."""

from __future__ import annotations

import pytest
from django.utils import timezone

from tests.helpers import add_member, login, make_household
from tracker.diapers import DIAPER_LABELS, create_diaper
from tracker.extra_models import Role

pytestmark = pytest.mark.django_db

LABELS = (
    ('wet', 'Wet'),
    ('dirty', 'Dirty'),
    ('both', 'Wet + dirty'),
    ('dry', 'Dry'),
)


def _page(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return response.content.decode()


def test_timeline_and_partial_show_each_diaper_label(client):
    household, baby, admin = make_household()
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_label_parent'
    )
    login(client, parent)
    when = timezone.now()
    events = {}
    for diaper_type, _label in LABELS:
        event, _replay = create_diaper(
            actor=parent,
            baby=baby,
            diaper_type=diaper_type,
            occurred_at=when,
            notes='',
            idempotency_key=f'label-{diaper_type}',
        )
        events[diaper_type] = event
    imported = events['dirty']
    imported.source_system = 'nara'
    imported.source_activity_id = 'act-label-dirty'
    imported.save(actor=parent, expected_version=imported.version)

    for path in ('/timeline/', '/timeline/partial/'):
        body = _page(client, path)
        for diaper_type, label in LABELS:
            assert f'class="diaper-type diaper-{diaper_type}"' in body
            assert f'class="diaper-label">{label}</span>' in body
            assert f'class="muted">{label}' not in body
        assert 'class="diaper-label">Dirty</span>' in body
        assert imported.source_system == 'nara'


def test_edit_and_history_use_the_same_labels(client):
    household, baby, admin = make_household()
    parent, _membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_label_edit'
    )
    login(client, parent)
    event, _replay = create_diaper(
        actor=parent,
        baby=baby,
        diaper_type='wet',
        occurred_at=timezone.now(),
        notes='',
        idempotency_key='label-edit',
    )
    edit = _page(client, f'/events/{event.pk}/edit/')
    assert 'class="diaper-label">Wet</span>' in edit
    for _value, label in DIAPER_LABELS.items():
        assert f'>{label}</option>' in edit
    assert 'value="wet" selected' in edit

    saved = client.post(
        f'/events/{event.pk}/edit/',
        {'version': event.version, 'diaper_type': 'both', 'notes': ''},
    )
    assert saved.status_code == 302
    history = _page(client, f'/events/{event.pk}/history/')
    assert 'class="diaper-label">Wet + dirty</span>' in history
    assert 'Type: Wet → Wet + dirty' in history
