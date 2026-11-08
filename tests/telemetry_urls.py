"""Routes used only by telemetry tests. Not mounted in the running app."""

from django.db import connection
from django.http import HttpResponse
from django.urls import path

from config.telemetry import record_functional_attributes


def sensitive_event(request, event_id):
    # The id is part of the route. It must not be copied onto the span.
    del event_id
    record_functional_attributes(event_type='diaper', role='caregiver')
    # Rejected: not on the functional allowlist, and must not be exported.
    record_functional_attributes(
        event_type='SyntheticBabyNameQq7',
        outcome='SyntheticNoteAlpha9f3c',
        role='parent.synthetic@example.com',
        error_class='parent.synthetic@example.com',
    )
    note = 'SyntheticNoteAlpha9f3c'
    with connection.cursor() as cursor:
        integration = getattr(
            connection.connection,
            '_self_db_api_integration',
            None,
        )
        previous = None
        if integration is not None:
            previous = integration.capture_parameters
            integration.capture_parameters = True
        try:
            cursor.execute('SELECT %s::text', [note])
            cursor.execute("SELECT 'SyntheticNoteAlpha9f3c'::text")
        finally:
            if integration is not None:
                integration.capture_parameters = previous
    if request.GET.get('boom') == '1':
        raise RuntimeError('SyntheticNoteAlpha9f3c in exception message')
    return HttpResponse('ok')


urlpatterns = [
    path('events/<int:event_id>/', sensitive_event),
]
