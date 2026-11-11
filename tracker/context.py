from django.conf import settings

from tracker.policy import can_admin, can_import


def shell(request):
    membership = getattr(request, 'membership', None)
    return {
        'nav_membership': membership,
        'can_import': can_import(membership),
        'can_admin_portal': can_admin(membership),
        'poll_ms': settings.TIMELINE_POLL_MS,
    }
