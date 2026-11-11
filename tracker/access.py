"""Invitations, sessions, and login throttling.

Passwords and sessions use Django's auth framework. Invite links are
`django.core.signing` tokens. The token is shown once and is not stored.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from tracker.extra_models import (
    AccountControl,
    Invitation,
    LoginThrottle,
    Membership,
    Role,
    TrackedSession,
)
from tracker.models import AuditEntry

INVITE_SALT = 'baby-tracker-invite'


def subject_hash(username: str) -> str:
    normalized = (username or '').strip().lower().encode()
    return hashlib.sha256(normalized).hexdigest()


def lockout_status(username: str, *, now=None):
    now = now or timezone.now()
    row = LoginThrottle.objects.filter(subject_hash=subject_hash(username)).first()
    if row is None or row.locked_until is None:
        return False
    if row.locked_until > now:
        return True
    return False


def record_login_failure(username: str, *, now=None) -> None:
    now = now or timezone.now()
    digest = subject_hash(username)
    window = timedelta(seconds=settings.LOGIN_WINDOW_SECONDS)
    row, _created = LoginThrottle.objects.get_or_create(
        subject_hash=digest,
        defaults={'failures': 0, 'window_started': now},
    )
    if row.window_started < now - window:
        row.failures = 0
        row.window_started = now
        row.locked_until = None
    row.failures += 1
    if row.failures >= settings.LOGIN_MAX_FAILURES:
        row.locked_until = now + timedelta(seconds=settings.LOGIN_LOCKOUT_SECONDS)
    row.save()


def clear_login_failures(username: str) -> None:
    LoginThrottle.objects.filter(subject_hash=subject_hash(username)).delete()


def active_admin_count(household) -> int:
    return Membership.objects.filter(
        household=household,
        role=Role.ADMIN,
        is_active=True,
    ).count()


def _would_remove_last_admin(membership, *, role, is_active) -> bool:
    if membership.role != Role.ADMIN or not membership.is_active:
        return False
    still_admin = is_active and role == Role.ADMIN
    if still_admin:
        return False
    return active_admin_count(membership.household) <= 1


@transaction.atomic
def revoke_all_sessions(user, *, actor, membership=None) -> None:
    control, _created = AccountControl.objects.select_for_update().get_or_create(
        user=user,
    )
    control.generation += 1
    control.save(update_fields=['generation'])
    keys = list(
        TrackedSession.objects.filter(user=user).values_list('session_key', flat=True)
    )
    if keys:
        Session.objects.filter(session_key__in=keys).delete()
        TrackedSession.objects.filter(user=user).delete()
    if membership is not None:
        AuditEntry.record(
            membership,
            actor=actor,
            action=AuditEntry.Action.UPDATE,
            changes={'sessions': {'before': 'active', 'after': 'revoked'}},
        )


def revoke_other_sessions(user, *, keep_session_key: str, actor, membership) -> int:
    others = TrackedSession.objects.filter(user=user).exclude(
        session_key=keep_session_key,
    )
    keys = list(others.values_list('session_key', flat=True))
    if keys:
        Session.objects.filter(session_key__in=keys).delete()
        others.delete()
    AuditEntry.record(
        membership,
        actor=actor,
        action=AuditEntry.Action.UPDATE,
        changes={'other_sessions': {'before': 'active', 'after': 'revoked'}},
    )
    return len(keys)


def bind_session(request, user, membership) -> None:
    control, _created = AccountControl.objects.get_or_create(user=user)
    request.session['session_generation'] = control.generation
    request.session['household_id'] = str(membership.household_id)
    request.session.save()
    TrackedSession.objects.update_or_create(
        session_key=request.session.session_key,
        defaults={'user': user},
    )


def drop_tracked_session(session_key: str | None) -> None:
    if session_key:
        TrackedSession.objects.filter(session_key=session_key).delete()


def create_invitation(*, actor, household, email, role, now=None):
    if role not in Role.values:
        raise ValidationError({'role': 'Choose admin, parent, or caregiver.'})
    now = now or timezone.now()
    invitation = Invitation(
        household=household,
        email=email.strip().lower(),
        role=role,
        expires_at=now + timedelta(hours=settings.INVITE_TTL_HOURS),
    )
    invitation.save(actor=actor)
    token = signing.dumps(str(invitation.pk), salt=INVITE_SALT)
    return invitation, token


def invitation_from_token(token: str):
    try:
        raw_pk = signing.loads(token, salt=INVITE_SALT)
    except signing.BadSignature:
        return None
    return Invitation.all_objects.filter(pk=raw_pk, deleted_at__isnull=True).first()


@transaction.atomic
def accept_invitation(*, token, password, now=None):
    now = now or timezone.now()
    invitation = invitation_from_token(token)
    if invitation is None or invitation.deleted_at is not None:
        raise ValidationError('This invitation link is not valid.')
    locked = Invitation.all_objects.select_for_update().get(pk=invitation.pk)
    if locked.accepted_at is not None:
        raise ValidationError('This invitation was already used.')
    if locked.expires_at <= now:
        raise ValidationError('This invitation has expired.')
    user_model = get_user_model()
    if user_model.objects.filter(email__iexact=locked.email).exists():
        raise ValidationError('An account with this email already exists.')
    username = _username_for_email(locked.email)
    user = user_model.objects.create_user(
        username=username,
        email=locked.email,
        password=password,
    )
    membership = Membership(
        household=locked.household,
        user=user,
        role=locked.role,
        is_active=True,
    )
    membership.save(actor=user)
    locked.accepted_at = now
    locked.save(actor=user, expected_version=locked.version)
    AccountControl.objects.get_or_create(user=user)
    return user, membership


def _username_for_email(email: str) -> str:
    user_model = get_user_model()
    base = email.split('@', 1)[0][:140] or 'member'
    candidate = base
    suffix = 1
    while user_model.objects.filter(username=candidate).exists():
        suffix += 1
        candidate = f'{base}-{suffix}'
    return candidate


@transaction.atomic
def update_membership(*, actor, membership, role, is_active: bool):
    if role not in Role.values:
        raise ValidationError({'role': 'Choose admin, parent, or caregiver.'})
    if _would_remove_last_admin(membership, role=role, is_active=is_active):
        raise ValidationError('The household needs at least one active admin.')
    role_changed = membership.role != role
    active_changed = membership.is_active != is_active
    membership.role = role
    membership.is_active = is_active
    membership.save(actor=actor, expected_version=membership.version)
    if role_changed or (active_changed and not is_active):
        revoke_all_sessions(membership.user, actor=actor, membership=membership)
    return membership


def sign_out_everywhere(*, actor, membership):
    revoke_all_sessions(membership.user, actor=actor, membership=membership)
