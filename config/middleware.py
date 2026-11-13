from django.http import HttpResponse


class HealthzMiddleware:
    """Serve probes before host and SSL checks.

    Orchestrator probes often send the pod IP (or another Host not in
    ALLOWED_HOSTS). Answering here avoids CommonMiddleware's 400.
    `/healthz` does not touch the database. `/readyz` does.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path.rstrip('/') or '/'
        if path == '/healthz':
            return HttpResponse('ok', content_type='text/plain')
        if path == '/readyz':
            from tracker.health import readyz

            return readyz(request)
        return self.get_response(request)


class ContentSecurityPolicyMiddleware:
    """Basic CSP for the deployed app. Pages do not need inline scripts."""

    POLICY = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self'; "
        "connect-src 'self'; "
        "manifest-src 'self'; "
        "worker-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault('Content-Security-Policy', self.POLICY)
        response.setdefault('Referrer-Policy', 'same-origin')
        return response
