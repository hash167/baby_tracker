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
from urllib.parse import urlsplit

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
    }
)
OUTCOMES = frozenset(
    {
        'success',
        'failure',
        'client_error',
        'server_error',
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

_ERROR_CLASS = re.compile(r'^[A-Z][A-Za-z0-9_]{0,80}$')
_SDK_VERSION_RE = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+-]*$')
_STATIC_SEGMENT = re.compile(r'^[a-z][a-z0-9_-]{0,32}$')
_DJANGO_CONVERTER = re.compile(r'<(?:int|str|slug|uuid|path):([A-Za-z_][A-Za-z0-9_]*)>')
_DJANGO_BARE = re.compile(r'<([A-Za-z_][A-Za-z0-9_]*)>')
_SQL_TOKEN = re.compile(r'^[A-Z]+')
_SAFE_ROUTE = re.compile(
    r'^/(?:$|[a-z][a-z0-9_-]{0,32}|\{[A-Za-z_][A-Za-z0-9_]*\})'
    r'(?:/(?:[a-z][a-z0-9_-]{0,32}|\{[A-Za-z_][A-Za-z0-9_]*\}))*/?$'
)
# Space is allowed in span names ("GET /healthz") and checked separately.
_LEAK_CHARS = frozenset('@?=&\n\r\t<>\'"`%')

_configured = False
_memory_exporter = None


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
    if isinstance(error_class, str) and _ERROR_CLASS.fullmatch(error_class):
        span.set_attribute('error.type', error_class)


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
    route = None
    if is_server:
        # Resolver templates only. A raw URL path is not a route.
        route = _route_from(original, span.name, templatize=False)
    elif is_client_http:
        route = _route_from(original, span.name, templatize=True)

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
        django.instrument(
            is_sql_commentor_enabled=False,
            response_hook=_django_response_hook,
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
        url = getattr(request, 'full_url', '') or ''
        method = ''
        get_method = getattr(request, 'get_method', None)
        if callable(get_method):
            method = get_method() or ''
        _rename_client_span(span, method, url)
    except Exception:
        logger.debug('urllib telemetry hook failed', exc_info=True)


def _urllib3_request_hook(span, instance, request_info) -> None:
    try:
        _rename_client_span(
            span,
            getattr(request_info, 'method', '') or '',
            getattr(request_info, 'url', '') or '',
        )
    except Exception:
        logger.debug('urllib3 telemetry hook failed', exc_info=True)


def _rename_client_span(span, method: str, url: str) -> None:
    if not span.is_recording():
        return
    safe_method = _method_from({}, method)
    route = _normalize_route(url, templatize=True) if url else None
    if safe_method and route:
        span.update_name(f'{safe_method} {route}')
        span.set_attribute('http.route', route)
    elif safe_method:
        span.update_name(safe_method)


def _route_for_request(request) -> str | None:
    path = getattr(request, 'path', '') or ''
    if path.rstrip('/') == '/healthz':
        return '/healthz'
    match = getattr(request, 'resolver_match', None)
    route = getattr(match, 'route', None) if match is not None else None
    if isinstance(route, str) and route:
        return _normalize_route(route, templatize=False)
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


def _route_from(
    attributes: Mapping[str, object],
    name: str,
    *,
    templatize: bool,
) -> str | None:
    candidates: list[str] = []
    keys = ('http.route', 'url.template')
    if templatize:
        # Client spans have no framework route. The path is templated and
        # the query string is discarded.
        keys = (
            'http.route',
            'url.template',
            'url.path',
            'url.full',
            'http.url',
            'http.target',
        )
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str):
            candidates.append(value)
    if name:
        candidates.append(name)
    for candidate in candidates:
        route = _normalize_route(candidate, templatize=templatize)
        if route:
            return route
    return None


def _normalize_route(candidate: str, *, templatize: bool = False) -> str | None:
    text = candidate.strip()
    if not text or any(char in text for char in '\n\r\x00'):
        return None
    pieces = text.split(' ', 1)
    if len(pieces) == 2 and pieces[0].upper() in HTTP_METHODS:
        text = pieces[1].strip()
    if '://' in text or text.startswith('//') or '@' in text:
        parsed = urlsplit(
            text if '://' in text or text.startswith('//') else 'http://' + text
        )
        text = parsed.path or '/'
    else:
        text = text.split('?', 1)[0].split('#', 1)[0]
    text = _DJANGO_CONVERTER.sub(lambda match: '{' + match.group(1) + '}', text)
    text = _DJANGO_BARE.sub(lambda match: '{' + match.group(1) + '}', text)
    if '<' in text or '>' in text:
        return None
    if not text.startswith('/'):
        text = '/' + text
    if _SAFE_ROUTE.fullmatch(text):
        return text
    if templatize:
        return _templatize_path(text)
    return None


def _templatize_path(path: str) -> str | None:
    raw = path.split('?', 1)[0].split('#', 1)[0]
    if not raw.startswith('/'):
        raw = '/' + raw
    segments = [segment for segment in raw.split('/') if segment]
    templated = [
        segment if _STATIC_SEGMENT.fullmatch(segment) else '{id}'
        for segment in segments
    ]
    if not templated:
        route = '/'
    else:
        route = '/' + '/'.join(templated)
        if raw.endswith('/') and route != '/':
            route += '/'
    if _SAFE_ROUTE.fullmatch(route):
        return route
    return None


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
    error = source.get('error.type')
    if isinstance(error, str) and _ERROR_CLASS.fullmatch(error):
        dest['error.type'] = error


def _error_class_from_events(events) -> str | None:
    for event in events or ():
        if event.name != 'exception':
            continue
        raw = dict(event.attributes or {})
        exc_type = raw.get('exception.type')
        if isinstance(exc_type, str):
            leaf = exc_type.rsplit('.', 1)[-1]
            if _ERROR_CLASS.fullmatch(leaf):
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
        exc_type = raw.get('exception.type')
        if isinstance(exc_type, str):
            leaf = exc_type.rsplit('.', 1)[-1]
            if _ERROR_CLASS.fullmatch(leaf):
                attrs['exception.type'] = leaf
        escaped = raw.get('exception.escaped')
        if escaped in ('True', 'False'):
            attrs['exception.escaped'] = escaped
        if not attrs:
            continue
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
    if name in DB_OPERATIONS or name in HTTP_METHODS or name == 'postgresql':
        return True
    method, _, rest = name.partition(' ')
    if method in HTTP_METHODS and rest and _SAFE_ROUTE.fullmatch(rest):
        return True
    return bool(re.fullmatch(r'[a-z][a-z0-9_.-]{0,40}', name))


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
