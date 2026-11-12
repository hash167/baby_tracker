"""OpenTelemetry traces for Django, psycopg, and outbound HTTP.

Spans are exported OTLP/HTTP only when a collector endpoint is configured.
The app does not read or send a vendor API key. Export filtering is
fail-closed: attributes, events, links, status descriptions, and resource
attributes that are not on the allowlist are removed before any exporter
sees them. Setup and export errors are logged and do not fail requests.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, SpanProcessor
from opentelemetry.sdk.version import __version__ as _SDK_VERSION
from opentelemetry.trace import Link, SpanContext, Status, StatusCode, TraceState

logger = logging.getLogger(__name__)

_SERVICE_NAME = 'baby-tracker'
_SDK_NAME = 'opentelemetry'
_SDK_LANGUAGE = 'python'

# Product vocabulary only. Identity (names, emails, ids) is never a value.
EVENT_TYPES = frozenset(
    {
        'diaper',
        'wet',
        'dirty',
        'both',
        'dry',
        'feed',
        'breastfeeding',
        'import',
        'invite',
        'account',
        'backup',
        'heartbeat',
    }
)
OUTCOMES = frozenset(
    {
        'success',
        'failure',
        'client_error',
        'server_error',
        'save_failed',
        'conflict',
    }
)
ROLES = frozenset({'anonymous', 'admin', 'parent', 'caregiver'})
HTTP_METHODS = frozenset(
    {
        'GET',
        'POST',
        'PUT',
        'PATCH',
        'DELETE',
        'HEAD',
        'OPTIONS',
        'TRACE',
        'HTTP',
    }
)
DB_OPERATIONS = frozenset(
    {
        'SELECT',
        'INSERT',
        'UPDATE',
        'DELETE',
        'MERGE',
        'BEGIN',
        'COMMIT',
        'ROLLBACK',
        'SAVEPOINT',
        'RELEASE',
        'SHOW',
        'SET',
        'CREATE',
        'ALTER',
        'DROP',
        'TRUNCATE',
        'CALL',
        'WITH',
    }
)

_SPAN_ATTRIBUTE_KEYS = frozenset(
    {
        'http.request.method',
        'http.response.status_code',
        'http.route',
        'db.system',
        'db.operation',
        'error.type',
        'app.event_type',
        'app.outcome',
        'app.role',
    }
)
_RESOURCE_KEYS = frozenset(
    {
        'service.name',
        'telemetry.sdk.name',
        'telemetry.sdk.language',
        'telemetry.sdk.version',
    }
)

# Env vars that tell upstream instrumentors to copy bodies, cookies, or headers
# onto spans. Removed at startup so they are not captured in the first place.
_CAPTURE_ENV_VARS = (
    'OTEL_PYTHON_DJANGO_TRACED_REQUEST_ATTRS',
    'OTEL_PYTHON_URLLIB_TRACED_REQUEST_ATTRS',
    'OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST',
    'OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE',
    'OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST',
    'OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_RESPONSE',
    'OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELDS',
)
_OTLP_HEADER_ENV_VARS = (
    'OTEL_EXPORTER_OTLP_HEADERS',
    'OTEL_EXPORTER_OTLP_TRACES_HEADERS',
)

_SDK_VERSION_RE = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+-]*$')
_DJANGO_CONVERTER = re.compile(r'<(?:int|str|slug|uuid|path):([A-Za-z_][A-Za-z0-9_]*)>')
_DJANGO_BARE = re.compile(r'<([A-Za-z_][A-Za-z0-9_]*)>')
_SQL_TOKEN = re.compile(r'^[A-Z]+')
_ROUTE_SEGMENT = re.compile(r'^(?:[A-Za-z0-9_.-]+|\{[A-Za-z_][A-Za-z0-9_]*\})$')
# Space is allowed in span names ("GET /healthz") and checked separately.
_LEAK_CHARS = frozenset('@?=&\n\r\t<>\'"`%')

_configured = False
_memory_exporter = None
_route_templates: dict[str, frozenset[str]] = {}
_error_classes: frozenset[str] | None = None


def safe_resource() -> Resource:
    """Resource that does not merge process args or OTEL_RESOURCE_ATTRIBUTES."""
    version = _SDK_VERSION if _SDK_VERSION_RE.fullmatch(_SDK_VERSION) else '0.0.0'
    return Resource(
        {
            'service.name': _SERVICE_NAME,
            'telemetry.sdk.name': _SDK_NAME,
            'telemetry.sdk.language': _SDK_LANGUAGE,
            'telemetry.sdk.version': version,
        }
    )


def record_functional_attributes(
    *,
    event_type: str | None = None,
    outcome: str | None = None,
    error_class: str | None = None,
    role: str | None = None,
) -> None:
    """Attach low-cardinality fields to the current span.

    Values outside the allowlists are ignored. This never records a name,
    email, id, note, or other identity.
    """
    span = trace.get_current_span()
    if not getattr(span, 'is_recording', lambda: False)():
        return
    if event_type in EVENT_TYPES:
        span.set_attribute('app.event_type', event_type)
    if outcome in OUTCOMES:
        span.set_attribute('app.outcome', outcome)
    if role in ROLES:
        span.set_attribute('app.role', role)
    error_leaf = _allowed_error_leaf(error_class)
    if error_leaf:
        span.set_attribute('error.type', error_leaf)


def otlp_traces_endpoint() -> str | None:
    """Collector URL from the environment, or None when export is off."""
    traces = os.environ.get('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT', '').strip()
    if traces:
        return traces
    base = os.environ.get('OTEL_EXPORTER_OTLP_ENDPOINT', '').strip()
    if not base:
        return None
    trimmed = base.rstrip('/')
    if trimmed.endswith('/v1/traces'):
        return trimmed
    return trimmed + '/v1/traces'


def build_otlp_exporter(endpoint: str):
    """OTLP/HTTP exporter for a collector URL.

    Header env vars are not forwarded. A vendor key set in the environment
    stays out of the export request.
    """
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    saved = {
        key: os.environ.pop(key) for key in _OTLP_HEADER_ENV_VARS if key in os.environ
    }
    try:
        import requests

        return OTLPSpanExporter(
            endpoint=endpoint,
            timeout=2,
            session=requests.Session(),
        )
    finally:
        os.environ.update(saved)


def setup_telemetry() -> bool:
    """Install tracing. Returns False if setup failed. Never raises."""
    global _configured
    if _configured:
        return True
    if os.environ.get('OTEL_SDK_DISABLED', '').strip().lower() in {
        '1',
        'true',
        'yes',
    }:
        _configured = True
        return True
    try:
        _setup_telemetry()
    except Exception:
        logger.warning(
            'OpenTelemetry setup failed; requests continue without telemetry',
            exc_info=True,
        )
        _configured = True
        return False
    _configured = True
    return True


def memory_exporter():
    """Attach one in-memory exporter to the live provider. Tests use this."""
    global _memory_exporter
    if _memory_exporter is None:
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        _memory_exporter = InMemorySpanExporter()
        provider = trace.get_tracer_provider()
        add = getattr(provider, 'add_span_processor', None)
        if add is None:
            raise RuntimeError('OpenTelemetry SDK provider is not installed')
        add(SimpleSpanProcessor(SanitizingSpanExporter(_memory_exporter)))
    return _memory_exporter


class SanitizingSpanProcessor(SpanProcessor):
    """Rewrite spans in place before later processors export them."""

    def on_start(self, span, parent_context=None) -> None:
        return None

    def on_end(self, span: ReadableSpan) -> None:
        try:
            redact_span(span)
        except Exception:
            logger.warning('span redaction failed; dropping span content')
            _wipe_span(span)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class SanitizingSpanExporter(SpanExporter):
    """Drop or redact spans again at the exporter boundary.

    A delegate that raises, or a collector that is down, becomes
    SpanExportResult.FAILURE instead of an exception on the request path.
    """

    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        safe: list[ReadableSpan] = []
        for span in spans:
            try:
                redact_span(span)
            except Exception:
                logger.warning('span redaction failed; span dropped')
                continue
            if _span_still_unsafe(span):
                logger.warning('span failed the privacy check; span dropped')
                continue
            safe.append(span)
        if not safe:
            return SpanExportResult.SUCCESS
        try:
            return self._delegate.export(safe)
        except Exception as exc:
            logger.warning('OpenTelemetry export failed: %s', type(exc).__name__)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._delegate.shutdown()
        except Exception as exc:
            logger.warning(
                'OpenTelemetry exporter shutdown failed: %s',
                type(exc).__name__,
            )

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return bool(self._delegate.force_flush(timeout_millis))
        except Exception as exc:
            logger.warning(
                'OpenTelemetry exporter flush failed: %s',
                type(exc).__name__,
            )
            return False


def redact_span(span: ReadableSpan) -> None:
    """Replace span fields with the allowlist. Idempotent."""
    original = dict(span.attributes or {})
    scope = _scope_name(span)
    is_db = _is_db_span(original, scope)
    is_server = 'django' in scope or span.kind is trace.SpanKind.SERVER and not is_db
    is_client_http = _is_client_http(scope, span.kind, is_db)

    method = _method_from(original, span.name)
    status_code = _status_from(original)
    route = _server_route(original, span.name) if is_server else None

    attributes: dict[str, str | int] = {}
    if method and (is_server or is_client_http or 'http.request.method' in original):
        attributes['http.request.method'] = method
    if status_code is not None and (is_server or is_client_http):
        attributes['http.response.status_code'] = status_code
    if route and (is_server or is_client_http):
        attributes['http.route'] = route
    if is_db:
        attributes['db.system'] = 'postgresql'
        operation = _db_operation(original, span.name)
        if operation:
            attributes['db.operation'] = operation
    _copy_functional(original, attributes)
    if 'error.type' not in attributes:
        error_class = _error_class_from_events(span.events)
        if error_class:
            attributes['error.type'] = error_class

    if is_db:
        span._name = attributes.get('db.operation', 'postgresql')
    elif (is_server or is_client_http) and method:
        span._name = f'{method} {route}' if route else method
    elif not _name_is_safe(span.name):
        span._name = 'span'

    span._attributes = attributes
    span._events = _redact_events(span.events)
    span._links = _redact_links(span.links)
    code = span.status.status_code if span.status is not None else StatusCode.UNSET
    span._status = Status(code)
    span._resource = _resource_from(span.resource)
    if span.context is not None:
        span._context = _clean_context(span.context)
    if span.parent is not None:
        span._parent = _clean_context(span.parent)


def _setup_telemetry() -> None:
    _disable_content_capture()
    provider = TracerProvider(resource=safe_resource())
    provider.add_span_processor(SanitizingSpanProcessor())
    endpoint = otlp_traces_endpoint()
    if endpoint:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(
            BatchSpanProcessor(
                SanitizingSpanExporter(build_otlp_exporter(endpoint)),
                schedule_delay_millis=5000,
                export_timeout_millis=2000,
            )
        )
    trace.set_tracer_provider(provider)
    _instrument()


def _disable_content_capture() -> None:
    for key in _CAPTURE_ENV_VARS:
        os.environ.pop(key, None)


def _instrument() -> None:
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.instrumentation.urllib import URLLibInstrumentor
    from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor

    django = DjangoInstrumentor()
    if not django.is_instrumented_by_opentelemetry:
        # HealthzMiddleware must stay first. This instrumentor inserts itself
        # at position 0 by default and then calls request.build_absolute_uri(),
        # which rejects a kubelet Host (the pod IP) before the probe middleware
        # can answer. Place it immediately after the probe middleware.
        from django.conf import settings

        probe = 'config.middleware.HealthzMiddleware'
        try:
            after_probe = list(settings.MIDDLEWARE).index(probe) + 1
        except ValueError:
            after_probe = 0
        django.instrument(
            is_sql_commentor_enabled=False,
            response_hook=_django_response_hook,
            middleware_position=after_probe,
        )
    psycopg = PsycopgInstrumentor()
    if not psycopg.is_instrumented_by_opentelemetry:
        psycopg.instrument(
            enable_commenter=False,
            enable_attribute_commenter=False,
            capture_parameters=False,
        )
    urllib = URLLibInstrumentor()
    if not urllib.is_instrumented_by_opentelemetry:
        urllib.instrument(
            request_hook=_urllib_request_hook,
            captured_request_headers=[],
            captured_response_headers=[],
        )
    urllib3 = URLLib3Instrumentor()
    if not urllib3.is_instrumented_by_opentelemetry:
        urllib3.instrument(
            request_hook=_urllib3_request_hook,
            captured_request_headers=[],
            captured_response_headers=[],
        )


def _django_response_hook(span, request, response) -> None:
    try:
        if not span.is_recording():
            return
        route = _route_for_request(request)
        method = _method_from({}, getattr(request, 'method', '') or '')
        if method and route:
            span.update_name(f'{method} {route}')
            span.set_attribute('http.route', route)
        elif method and not _name_is_safe(span.name):
            span.update_name(method)
        status_code = getattr(response, 'status_code', None)
        outcome = _outcome_for_status(status_code)
        if outcome and span.attributes.get('app.outcome') not in OUTCOMES:
            span.set_attribute('app.outcome', outcome)
        if span.attributes.get('app.role') not in ROLES:
            role = _role_from_request(request)
            if role:
                span.set_attribute('app.role', role)
    except Exception:
        logger.debug('telemetry response hook failed', exc_info=True)


def _urllib_request_hook(span, request) -> None:
    try:
        method = ''
        get_method = getattr(request, 'get_method', None)
        if callable(get_method):
            method = get_method() or ''
        _rename_client_span(span, method)
    except Exception:
        logger.debug('urllib telemetry hook failed', exc_info=True)


def _urllib3_request_hook(span, instance, request_info) -> None:
    try:
        _rename_client_span(span, getattr(request_info, 'method', '') or '')
    except Exception:
        logger.debug('urllib3 telemetry hook failed', exc_info=True)


def _rename_client_span(span, method: str) -> None:
    """Name client spans with the method only.

    Outbound URLs do not come from a route table. A path segment is not a
    template just because it is lowercase, so the path is left off the span.
    """
    if not span.is_recording():
        return
    safe_method = _method_from({}, method)
    if safe_method:
        span.update_name(safe_method)


def _route_for_request(request) -> str | None:
    path = getattr(request, 'path', '') or ''
    bare = path.rstrip('/')
    if bare == '/healthz':
        candidate = '/healthz'
    elif bare == '/readyz':
        candidate = '/readyz'
    else:
        match = getattr(request, 'resolver_match', None)
        route = getattr(match, 'route', None) if match is not None else None
        if not isinstance(route, str) or not route:
            return None
        candidate = _normalize_django_route(route)
    if candidate and candidate in known_django_route_templates():
        return candidate
    return None


def _role_from_request(request) -> str | None:
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return 'anonymous'
    raw = getattr(user, 'role', None)
    if isinstance(raw, str) and raw in ROLES:
        return raw
    return None


def _outcome_for_status(status_code: object) -> str | None:
    if not isinstance(status_code, int):
        return None
    if 100 <= status_code <= 399:
        return 'success'
    if 400 <= status_code <= 499:
        return 'client_error'
    if 500 <= status_code <= 599:
        return 'server_error'
    return None


def _scope_name(span: ReadableSpan) -> str:
    scope = span.instrumentation_scope
    if scope is None:
        return ''
    return scope.name or ''


def _is_db_span(attributes: Mapping[str, object], scope: str) -> bool:
    if 'psycopg' in scope or scope.endswith('dbapi'):
        return True
    return any(
        key in attributes
        for key in (
            'db.system',
            'db.system.name',
            'db.statement',
            'db.query.text',
            'db.statement.parameters',
            'db.operation',
            'db.operation.name',
        )
    )


def _is_client_http(scope: str, kind, is_db: bool) -> bool:
    if is_db:
        return False
    if 'urllib' in scope:
        return True
    return (
        kind is trace.SpanKind.CLIENT
        and 'django' not in scope
        and 'psycopg' not in scope
    )


def _method_from(attributes: Mapping[str, object], name: str) -> str | None:
    for key in ('http.request.method', 'http.method'):
        value = attributes.get(key)
        if isinstance(value, str) and value.upper() in HTTP_METHODS:
            return value.upper()
    token = (name or '').split(' ', 1)[0].upper()
    if token in HTTP_METHODS:
        return token
    return None


def _status_from(attributes: Mapping[str, object]) -> int | None:
    for key in ('http.response.status_code', 'http.status_code'):
        value = attributes.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and 100 <= value <= 599:
            return value
        if isinstance(value, str) and value.isdigit():
            code = int(value)
            if 100 <= code <= 599:
                return code
    return None


def known_django_route_templates() -> frozenset[str]:
    """Route templates declared in the current URLconf, plus /healthz.

    These strings come from URL patterns, not from request or client paths.
    """
    from django.urls import get_resolver, get_urlconf

    urlconf = get_urlconf() or ''
    cached = _route_templates.get(urlconf)
    if cached is not None:
        return cached
    found = {'/healthz'}
    try:
        resolver = get_resolver(urlconf) if urlconf else get_resolver()
        _collect_routes(resolver.url_patterns, '', found)
    except Exception:
        logger.debug('could not read Django URL patterns', exc_info=True)
        return frozenset(found)
    frozen = frozenset(found)
    _route_templates[urlconf] = frozen
    return frozen


def _collect_routes(patterns, prefix: str, found: set[str]) -> None:
    for pattern in patterns:
        piece = ''
        try:
            route_pattern = pattern.pattern
            raw = getattr(route_pattern, '_route', None)
            piece = raw if isinstance(raw, str) else str(route_pattern)
        except Exception:
            piece = ''
        combined = f'{prefix}{piece}'
        nested = getattr(pattern, 'url_patterns', None)
        if nested is not None:
            _collect_routes(nested, combined, found)
            continue
        normalized = _normalize_django_route(combined)
        if normalized:
            found.add(normalized)


def _server_route(attributes: Mapping[str, object], name: str) -> str | None:
    known = known_django_route_templates()
    candidates: list[str] = []
    route = attributes.get('http.route')
    if isinstance(route, str):
        candidates.append(route)
    if name:
        candidates.append(name)
    for candidate in candidates:
        normalized = _normalize_django_route(candidate)
        if normalized in known:
            return normalized
    return None


def _normalize_django_route(candidate: str) -> str | None:
    """Turn a Django route pattern into /events/{event_id}/ form.

    This does not invent placeholders for path segments. A value is exported
    only when it is also one of known_django_route_templates().
    """
    text = candidate.strip()
    if not text or any(char in text for char in '\n\r\x00'):
        return None
    pieces = text.split(' ', 1)
    if len(pieces) == 2 and pieces[0].upper() in HTTP_METHODS:
        text = pieces[1].strip()
    if '://' in text or '@' in text or '?' in text or '#' in text:
        return None
    text = _DJANGO_CONVERTER.sub(lambda match: '{' + match.group(1) + '}', text)
    text = _DJANGO_BARE.sub(lambda match: '{' + match.group(1) + '}', text)
    if any(char in text for char in '<>@?=&\n\r\t\'"`%'):
        return None
    if not text.startswith('/'):
        text = '/' + text
    if '//' in text or ' ' in text:
        return None
    segments = [segment for segment in text.split('/') if segment]
    if any(_ROUTE_SEGMENT.fullmatch(segment) is None for segment in segments):
        return None
    if text != '/' and not text.startswith('/'):
        return None
    return text


def _db_operation(attributes: Mapping[str, object], name: str) -> str | None:
    for key in ('db.operation', 'db.operation.name'):
        value = attributes.get(key)
        if isinstance(value, str) and value.upper() in DB_OPERATIONS:
            return value.upper()
    for key in ('db.statement', 'db.query.text', 'db.query.summary'):
        value = attributes.get(key)
        if isinstance(value, str):
            operation = _sql_operation(value)
            if operation:
                return operation
    return _sql_operation(name)


def _sql_operation(text: str) -> str | None:
    stripped = text.strip()
    while stripped.startswith('--') or stripped.startswith('/*'):
        if stripped.startswith('--'):
            newline = stripped.find('\n')
            if newline == -1:
                return None
            stripped = stripped[newline + 1 :].lstrip()
        else:
            end = stripped.find('*/')
            if end == -1:
                return None
            stripped = stripped[end + 2 :].lstrip()
    if not stripped:
        return None
    token = stripped.split(None, 1)[0].strip('`"\'(')
    match = _SQL_TOKEN.match(token.upper())
    if match and match.group(0) in DB_OPERATIONS:
        return match.group(0)
    return None


def _copy_functional(source: Mapping[str, object], dest: dict[str, str | int]) -> None:
    event_type = source.get('app.event_type')
    if isinstance(event_type, str) and event_type in EVENT_TYPES:
        dest['app.event_type'] = event_type
    outcome = source.get('app.outcome')
    if isinstance(outcome, str) and outcome in OUTCOMES:
        dest['app.outcome'] = outcome
    role = source.get('app.role')
    if isinstance(role, str) and role in ROLES:
        dest['app.role'] = role
    error_leaf = _allowed_error_leaf(source.get('error.type'))
    if error_leaf:
        dest['error.type'] = error_leaf


def allowed_error_classes() -> frozenset[str]:
    """Exception class names defined by the runtime, Django, psycopg, and urllib.

    Membership is the class object __name__, not a character pattern. A note
    or name that merely looks like a class is not in this set.
    """
    global _error_classes
    if _error_classes is None:
        _error_classes = _load_error_classes()
    return _error_classes


def _load_error_classes() -> frozenset[str]:
    import builtins
    import importlib

    names: set[str] = set()
    modules: list[object] = [builtins]
    for module_name in (
        'django.core.exceptions',
        'django.db',
        'django.db.utils',
        'django.http',
        'psycopg',
        'urllib.error',
        'http.client',
    ):
        imported = None
        try:
            imported = importlib.import_module(module_name)
        except Exception:
            imported = None
        if imported is not None:
            modules.append(imported)
    for module in modules:
        for value in vars(module).values():
            if not isinstance(value, type):
                continue
            is_exception = False
            try:
                is_exception = issubclass(value, BaseException)
            except TypeError:
                is_exception = False
            if is_exception and value.__name__:
                names.add(value.__name__)
    return frozenset(names)


def _allowed_error_leaf(value: object) -> str | None:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        return None
    leaf = value.rsplit('.', 1)[-1]
    if leaf in allowed_error_classes():
        return leaf
    return None


def _error_class_from_events(events) -> str | None:
    for event in events or ():
        if event.name != 'exception':
            continue
        raw = dict(event.attributes or {})
        leaf = _allowed_error_leaf(raw.get('exception.type'))
        if leaf:
            return leaf
    return None


def _redact_events(events) -> tuple:
    from opentelemetry.sdk.trace import Event

    safe = []
    for event in events or ():
        if event.name != 'exception':
            continue
        attrs = {}
        raw = dict(event.attributes or {})
        leaf = _allowed_error_leaf(raw.get('exception.type'))
        if not leaf:
            continue
        attrs['exception.type'] = leaf
        escaped = raw.get('exception.escaped')
        if escaped in ('True', 'False'):
            attrs['exception.escaped'] = escaped
        safe.append(
            Event(name='exception', attributes=attrs, timestamp=event.timestamp)
        )
    return tuple(safe)


def _redact_links(links) -> tuple:
    safe = []
    for link in links or ():
        context = getattr(link, 'context', None)
        if context is None:
            continue
        safe.append(Link(_clean_context(context), attributes={}))
    return tuple(safe)


def _resource_from(resource) -> Resource:
    source = dict(resource.attributes) if resource is not None else {}
    attributes = {
        'service.name': _SERVICE_NAME,
        'telemetry.sdk.name': _SDK_NAME,
        'telemetry.sdk.language': _SDK_LANGUAGE,
    }
    version = source.get('telemetry.sdk.version', _SDK_VERSION)
    if isinstance(version, str) and _SDK_VERSION_RE.fullmatch(version):
        attributes['telemetry.sdk.version'] = version
    else:
        attributes['telemetry.sdk.version'] = safe_resource().attributes[
            'telemetry.sdk.version'
        ]
    return Resource(attributes)


def _clean_context(context: SpanContext) -> SpanContext:
    trace_state = context.trace_state
    if trace_state is None or len(trace_state) == 0:
        return context
    return SpanContext(
        context.trace_id,
        context.span_id,
        context.is_remote,
        context.trace_flags,
        TraceState(),
    )


def _name_is_safe(name: str) -> bool:
    if name in DB_OPERATIONS or name in HTTP_METHODS or name in {'postgresql', 'span'}:
        return True
    method, _, rest = name.partition(' ')
    if method not in HTTP_METHODS or not rest:
        return False
    normalized = _normalize_django_route(rest)
    return bool(normalized and normalized in known_django_route_templates())


def _wipe_span(span: ReadableSpan) -> None:
    span._name = 'span'
    span._attributes = {}
    span._events = ()
    span._links = ()
    span._status = Status(StatusCode.UNSET)
    span._resource = safe_resource()
    if span.context is not None:
        span._context = _clean_context(span.context)


def _span_still_unsafe(span: ReadableSpan) -> bool:
    """Fail closed if any retained string still looks like payload data."""
    try:
        if not _name_is_safe(span.name):
            return True
        values: list[str] = []
        values.extend(_strings(span.attributes))
        for event in span.events:
            if event.name != 'exception':
                return True
            values.extend(_strings(event.attributes))
        for link in span.links:
            values.extend(_strings(getattr(link, 'attributes', None)))
            state = getattr(getattr(link, 'context', None), 'trace_state', None)
            if state is not None and len(state) > 0:
                return True
        if span.status is not None and span.status.description:
            return True
        if span.resource is not None:
            values.extend(_strings(span.resource.attributes))
            if not set(span.resource.attributes).issubset(_RESOURCE_KEYS):
                return True
        for text in values:
            if any(char.isspace() for char in text):
                return True
            if any(char in text for char in _LEAK_CHARS):
                return True
            lowered = text.lower()
            if 'bearer' in lowered or 'cookie' in lowered or 'password' in lowered:
                return True
        return not set(span.attributes or {}).issubset(_SPAN_ATTRIBUTE_KEYS)
    except Exception:
        return True


def _strings(value) -> list[str]:
    found: list[str] = []

    def walk(item) -> None:
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, Mapping):
            for key, nested in item.items():
                walk(key)
                walk(nested)
        elif isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray)):
            for nested in item:
                walk(nested)

    walk(value)
    return found
