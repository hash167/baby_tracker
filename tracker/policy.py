"""Server-side household authorization.

Button visibility is not a check. Every view calls these helpers.
"""

from __future__ import annotations

from tracker.extra_models import Membership, Role
from tracker.models import Event


def membership_for(user, household):
    if not getattr(user, 'is_authenticated', False):
        return None
    if household is None:
        return None
    return (
        Membership.objects.filter(
            user=user,
            household=household,
            is_active=True,
        )
        .select_related('household', 'user')
        .first()
    )


def _role(membership) -> str | None:
    if membership is None:
        return None
    return membership.role


def can_view(membership) -> bool:
    return _role(membership) in (Role.ADMIN, Role.PARENT, Role.CAREGIVER)


def can_create(membership) -> bool:
    return can_view(membership)


def can_edit_event(membership, event) -> bool:
    role = _role(membership)
    if role in (Role.ADMIN, Role.PARENT):
        return True
    if role == Role.CAREGIVER:
        return event.created_by_id == membership.user_id
    return False


def can_delete_event(membership, event) -> bool:
    return can_edit_event(membership, event)


def can_restore_event(membership, event) -> bool:
    del event
    return _role(membership) in (Role.ADMIN, Role.PARENT)


def can_import(membership) -> bool:
    return _role(membership) in (Role.ADMIN, Role.PARENT)


def can_admin(membership) -> bool:
    return _role(membership) == Role.ADMIN


def same_household(membership, event) -> bool:
    if membership is None or event is None:
        return False
    return event.household_id == membership.household_id


VISIBLE_KINDS = (
    Event.Kind.DIAPER,
    Event.Kind.FEED,
    Event.Kind.BOTTLE,
    Event.Kind.PUMP,
)
ARCHIVED_KINDS = (
    Event.Kind.SLEEP,
    Event.Kind.GROWTH,
    Event.Kind.MEDICAL,
    Event.Kind.UNKNOWN,
)
