from django.http import HttpResponse


class HealthzMiddleware:
    """Serve /healthz before host and SSL checks.

    Orchestrator probes often send the pod IP (or another Host not in
    ALLOWED_HOSTS). Answering here avoids CommonMiddleware's 400.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.rstrip('/') == '/healthz':
            return HttpResponse('ok', content_type='text/plain')
        return self.get_response(request)
