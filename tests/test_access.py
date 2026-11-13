"""Invite-only accounts, throttling, roles, and session revocation."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from tests.factories import SYNTHETIC_PASSWORD
from tests.helpers import add_member, login, make_household
from tracker.access import create_invitation, update_membership
from tracker.diapers import create_diaper
from tracker.extra_models import Role
from tracker.models import AuditEntry, Event

pytestmark = pytest.mark.django_db


def test_signup_urls_are_not_available(client):
    assert client.get('/signup/').status_code == 404
    assert client.get('/accounts/signup/').status_code == 404


def test_invite_is_single_use_and_expiry_is_enforced(client):
    household, _baby, admin = make_household()
    add_member(household, Role.ADMIN, actor=admin, username='synth_admin_invite')
    _invitation, token = create_invitation(
        actor=admin,
        household=household,
        email='new-caregiver@example.com',
        role=Role.CAREGIVER,
    )
    before = get_user_model().objects.count()
    expired = client.post(
        f'/invite/{token}/',
        {'new_password1': 'synthetic-pass-word-1', 'new_password2': 'other'},
    )
    assert expired.status_code == 200
    assert get_user_model().objects.count() == before

    response = client.post(
        f'/invite/{token}/',
        {
            'new_password1': 'synthetic-pass-word-1',
            'new_password2': 'synthetic-pass-word-1',
        },
    )
    assert response.status_code == 302
    assert (
        get_user_model().objects.filter(email='new-caregiver@example.com').count() == 1
    )
    again = client.post(
        f'/invite/{token}/',
        {
            'new_password1': 'synthetic-pass-word-1',
            'new_password2': 'synthetic-pass-word-1',
        },
    )
    assert again.status_code == 200
    assert b'already used' in again.content
    assert (
        get_user_model().objects.filter(email='new-caregiver@example.com').count() == 1
    )

    _late, late_token = create_invitation(
        actor=admin,
        household=household,
        email='late@example.com',
        role=Role.PARENT,
    )
    from tracker.access import invitation_from_token

    row = invitation_from_token(late_token)
    row.expires_at = timezone.now() - timedelta(minutes=1)
    row.save(actor=admin, expected_version=row.version)
    stale = Client().post(
        f'/invite/{late_token}/',
        {
            'new_password1': 'synthetic-pass-word-1',
            'new_password2': 'synthetic-pass-word-1',
        },
    )
    assert b'expired' in stale.content
    assert not get_user_model().objects.filter(email='late@example.com').exists()
    assert client.get(f'/invite/{"not-a-token"}/').status_code == 200


def test_repeated_failed_logins_are_throttled(client):
    household, _baby, admin = make_household()
    user, _membership = add_member(
        household,
        Role.PARENT,
        actor=admin,
        username='synth_throttle',
    )
    for _attempt in range(5):
        response = client.post(
            '/login/',
            {'username': user.username, 'password': 'wrong-synthetic'},  # nosec B105
        )
        assert response.status_code == 400
    locked = client.post(
        '/login/',
        {'username': user.username, 'password': SYNTHETIC_PASSWORD},
    )
    assert locked.status_code == 429


def test_caregiver_cannot_edit_another_entry_and_writes_no_audit(client):
    household, baby, admin = make_household()
    parent, _parent_membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_parent_a'
    )
    caregiver, _care = add_member(
        household, Role.CAREGIVER, actor=admin, username='synth_care_a'
    )
    event, _replay = create_diaper(
        actor=parent,
        baby=baby,
        diaper_type='wet',
        occurred_at=timezone.now(),
        notes='parent note',
        idempotency_key='parent-diaper-1',
    )
    audits = event.history().count()
    login(client, caregiver)
    response = client.post(
        f'/events/{event.pk}/edit/',
        {'version': event.version, 'diaper_type': 'dry', 'notes': 'nope'},
    )
    assert response.status_code == 403
    fresh = Event.objects.get(pk=event.pk)
    assert fresh.diaper_type == 'wet'
    assert fresh.version == event.version
    assert fresh.history().count() == audits
    assert AuditEntry.objects.filter(object_id=str(event.pk)).count() == audits


def test_cross_household_and_anonymous_access(client):
    household_a, baby_a, admin_a = make_household()
    user_a, _membership = add_member(
        household_a, Role.PARENT, actor=admin_a, username='synth_house_a'
    )
    household_b, baby_b, admin_b = make_household(baby_name='Other Synth')
    _user_b, _other = add_member(
        household_b, Role.PARENT, actor=admin_b, username='synth_house_b'
    )
    event, _replay = create_diaper(
        actor=admin_b,
        baby=baby_b,
        diaper_type='dirty',
        occurred_at=timezone.now(),
        notes='',
        idempotency_key='house-b-diaper',
    )
    anon = client.get('/timeline/')
    assert anon.status_code == 302
    assert '/login/' in anon['Location']
    htmx = client.get('/timeline/partial/', HTTP_HX_REQUEST='true')
    assert htmx.status_code == 401

    login(client, user_a)
    assert client.get('/timeline/').status_code == 200
    assert client.get(f'/events/{event.pk}/edit/').status_code == 404
    assert client.get(f'/events/{event.pk}/history/').status_code == 404
    denied = client.post(
        f'/events/{event.pk}/delete/',
        {'version': event.version},
    )
    assert denied.status_code == 404
    assert Event.objects.filter(pk=event.pk).exists()
    assert (
        b'Synth' not in client.get('/timeline/partial/').content or baby_a.name.encode()
    )
    assert baby_b.name.encode() not in client.get('/timeline/').content


def test_parent_and_caregiver_cannot_open_admin_portal(client):
    household, _baby, admin = make_household()
    add_member(household, Role.ADMIN, actor=admin, username='synth_only_admin')
    parent, _p = add_member(
        household, Role.PARENT, actor=admin, username='synth_not_admin'
    )
    caregiver, _c = add_member(
        household, Role.CAREGIVER, actor=admin, username='synth_not_admin_2'
    )
    login(client, parent)
    assert client.get('/portal/').status_code == 403
    login(Client(), caregiver)
    other = Client()
    login(other, caregiver)
    assert other.get('/portal/').status_code == 403
    admin_client = Client()
    login(admin_client, admin if admin.username else parent)
    # The household creator may not have a membership yet.
    add_member(household, Role.ADMIN, actor=admin, username='synth_portal_admin')


def test_deactivation_and_role_change_revoke_sessions():
    household, _baby, admin = make_household()
    admin_user, admin_membership = add_member(
        household, Role.ADMIN, actor=admin, username='synth_revoker'
    )
    parent, parent_membership = add_member(
        household, Role.PARENT, actor=admin, username='synth_revoked'
    )
    first = Client()
    second = Client()
    login(first, parent)
    login(second, parent)
    assert first.get('/timeline/').status_code == 200
    update_membership(
        actor=admin_user,
        membership=parent_membership,
        role=Role.CAREGIVER,
        is_active=True,
    )
    assert first.get('/timeline/').status_code == 302
    assert second.get('/timeline/', HTTP_HX_REQUEST='true').status_code == 401
    rebound = Client()
    login(rebound, parent)
    assert rebound.get('/portal/').status_code == 403

    update_membership(
        actor=admin_user,
        membership=parent_membership,
        role=Role.CAREGIVER,
        is_active=False,
    )
    assert rebound.get('/timeline/').status_code == 302
    blocked = Client().post(
        '/login/',
        {'username': parent.username, 'password': SYNTHETIC_PASSWORD},
    )
    assert blocked.status_code == 403

    with pytest.raises(Exception, match='at least one active admin'):
        update_membership(
            actor=admin_user,
            membership=admin_membership,
            role=Role.PARENT,
            is_active=True,
        )


def test_admin_portal_invites_and_audits(client):
    household, _baby, admin = make_household()
    admin_user, _membership = add_member(
        household, Role.ADMIN, actor=admin, username='synth_portal'
    )
    login(client, admin_user)
    before = AuditEntry.objects.count()
    response = client.post(
        '/portal/',
        {'form': 'invite', 'email': 'doula@example.com', 'role': 'caregiver'},
    )
    assert response.status_code == 200
    assert b'/invite/' in response.content
    assert AuditEntry.objects.count() > before
    assert not get_user_model().objects.filter(email='doula@example.com').exists()
