from django.conf import settings
from django.utils import timezone

from tracker.days import local_date
from tracker.policy import can_admin, can_import


def shell(request):
    membership = getattr(request, 'membership', None)
    household = getattr(request, 'household', None)
    baby = None
    today = ''
    if household is not None:
        babies = list(household.babies.all())
        selected = request.session.get('baby_id')
        for item in babies:
            if str(item.pk) == str(selected):
                baby = item
                break
        if baby is None and babies:
            baby = babies[0]
        day = local_date(timezone.now(), household.timezone)
        today = f'{day.strftime("%A, %b")} {day.day}'
    return {
        'nav_membership': membership,
        'nav_baby': baby,
        'nav_today': today,
        'can_import': can_import(membership),
        'can_admin_portal': can_admin(membership),
        'poll_ms': settings.TIMELINE_POLL_MS,
    }
