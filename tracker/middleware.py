"""Household role, session generation, and display timezone.

The role is read from the database on every request. It is not cached in
the session beyond the household id and the session generation.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from tracker.access import drop_tracked_session
from tracker.extra_models import AccountControl
from tracker.policy import membership_for


class HouseholdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.membership = None
        request.household = None
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            if not _generation_matches(request, user):
                return _reject(request)
            membership = _load_membership(request, user)
            if membership is None:
                return _reject(request)
            request.membership = membership
            request.household = membership.household
            user.role = membership.role
            timezone.activate(ZoneInfo(membership.household.timezone))
        try:
            response = self.get_response(request)
        finally:
            timezone.deactivate()
        if getattr(request, 'user', None) is not None and request.user.is_authenticated:
            response['Cache-Control'] = 'no-store'
        return response


def _generation_matches(request, user) -> bool:
    control, _created = AccountControl.objects.get_or_create(user=user)
    stored = request.session.get('session_generation')
    return stored == control.generation


def _load_membership(request, user):
    household_id = request.session.get('household_id')
    if not household_id:
        return None
    from tracker.models import Household

    household = Household.objects.filter(pk=household_id).first()
    return membership_for(user, household)


def _reject(request):
    session_key = request.session.session_key
    logout(request)
    drop_tracked_session(session_key)
    if request.headers.get('HX-Request'):
        response = HttpResponse(status=401)
        response['HX-Redirect'] = reverse('login')
        return response
    return redirect('login')
