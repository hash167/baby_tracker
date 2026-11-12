import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceState

from config.telemetry import (
    SanitizingSpanExporter,
    build_otlp_exporter,
    memory_exporter,
    safe_resource,
    setup_telemetry,
)

NOTE = 'SyntheticNoteAlpha9f3c'
BABY = 'SyntheticBabyNameQq7'
CAREGIVER = 'SyntheticCaregiverWw2'
EMAIL = 'parent.synthetic@example.com'
TOKEN = 'synthetic-bearer-token'  # nosec B105
COOKIE = 'synthetic-cookie-value'
IMPORT_ROW = 'SyntheticImportCsvRow77'
# Lowercase on purpose: a letter-only path segment is still a name or note.
LOWER_NAME = 'syntheticbabynameqq7'
LOWER_NOTE = 'syntheticnotelowercase'
LOWER_CAREGIVER = 'syntheticcaregiverww2'
SECRETS = (
    NOTE,
    BABY,
    CAREGIVER,
    EMAIL,
    TOKEN,
    COOKIE,
    IMPORT_ROW,
    LOWER_NAME,
    LOWER_NOTE,
    LOWER_CAREGIVER,
)

RAW_EVENT_PATH = '/events/42/'
RAW_OUTBOUND_PATH = f'/babies/{LOWER_NAME}/notes/{LOWER_NOTE}/{LOWER_CAREGIVER}'


@pytest.fixture
def exported_spans():
    exporter = memory_exporter()
    exporter.clear()
    yield exporter
    exporter.clear()


def _span_text(spans) -> str:
    chunks: list[str] = []

    def add(value) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                add(key)
                add(item)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                add(item)
            return
        chunks.append(str(value))

    for span in spans:
        add(span.name)
        add(dict(span.attributes))
        for event in span.events:
            add(event.name)
            add(dict(event.attributes or {}))
        for link in span.links:
            add(dict(link.attributes or {}))
            context = getattr(link, 'context', None)
            if context is not None and context.trace_state:
                add(repr(context.trace_state))
        if span.status is not None:
            add(span.status.description)
        add(dict(span.resource.attributes))
        if span.context is not None and span.context.trace_state:
            add(repr(span.context.trace_state))
        if span.parent is not None and span.parent.trace_state:
            add(repr(span.parent.trace_state))
        scope = span.instrumentation_scope
        if scope is not None:
            add(scope.name)
            add(getattr(scope, 'attributes', None))
        add(span.to_json())
    return '\n'.join(chunks)


def _assert_secrets_absent(spans) -> None:
    blob = _span_text(spans)
    for secret in SECRETS:
        assert secret not in blob
    assert RAW_EVENT_PATH not in blob
    assert RAW_OUTBOUND_PATH not in blob
    assert 'note=' not in blob
    assert 'db.statement' not in blob
    assert 'db.query.text' not in blob
    assert 'parameters' not in blob


def _enable_sensitive_capture(monkeypatch) -> None:
    """Turn on upstream capture so the filter has something to remove."""
    monkeypatch.setenv(
        'OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST',
        '.*',
    )
    monkeypatch.setenv(
        'OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE',
        '.*',
    )
    from opentelemetry.instrumentation.django.middleware import (
        otel_middleware,
    )

    # body before POST: Django refuses body after the POST stream is read.
    otel_middleware._DjangoMiddleware._traced_request_attrs = [
        'body',
        'POST',
        'GET',
        'COOKIES',
        'META',
        'FILES',
        'path',
    ]


def _restore_capture() -> None:
    from opentelemetry.instrumentation.django.middleware import (
        otel_middleware,
    )

    otel_middleware._DjangoMiddleware._traced_request_attrs = []


@pytest.mark.django_db
def test_sensitive_request_and_db_spans_keep_route_status_and_timing(
    client,
    exported_spans,
    monkeypatch,
):
    _enable_sensitive_capture(monkeypatch)
    upload = SimpleUploadedFile(
        'import.csv',
        f'name,note\n{BABY},{IMPORT_ROW}\n'.encode(),
        content_type='text/csv',
    )
    try:
        with override_settings(ROOT_URLCONF='tests.telemetry_urls'):
            response = client.post(
                f'{RAW_EVENT_PATH}?note={NOTE}&email={EMAIL}&baby={BABY}',
                data={
                    'note': NOTE,
                    'baby_name': BABY,
                    'caregiver_name': CAREGIVER,
                    'email': EMAIL,
                    'import_file': upload,
                },
                HTTP_AUTHORIZATION=f'Bearer {TOKEN}',
                HTTP_COOKIE=f'sessionid={COOKIE}',
                HTTP_X_CAREGIVER_NAME=CAREGIVER,
            )
    finally:
        _restore_capture()

    assert response.status_code == 200
    spans = exported_spans.get_finished_spans()
    _assert_secrets_absent(spans)

    server = [
        span
        for span in spans
        if span.attributes.get('http.route') == '/events/{event_id}/'
    ]
    assert server, [span.name for span in spans]
    request_span = server[0]
    assert request_span.name == 'POST /events/{event_id}/'
    assert request_span.attributes['http.request.method'] == 'POST'
    assert request_span.attributes['http.response.status_code'] == 200
    assert request_span.attributes['app.event_type'] == 'diaper'
    assert request_span.attributes['app.outcome'] == 'success'
    assert request_span.attributes['app.role'] == 'caregiver'
    assert 'email' not in request_span.attributes
    assert 'error.type' not in request_span.attributes
    assert request_span.status.description in (None, '')

    db_spans = [
        span
        for span in spans
        if span.attributes.get('db.system') == 'postgresql'
        and span.attributes.get('db.operation') == 'SELECT'
    ]
    assert db_spans, [span.name for span in spans]
    for span in db_spans:
        assert span.name == 'SELECT'
        assert span.start_time is not None
        assert span.end_time is not None
        assert span.end_time >= span.start_time
        assert 'db.statement' not in span.attributes
        assert 'db.user' not in span.attributes
        assert 'db.name' not in span.attributes


@pytest.mark.django_db
def test_exception_span_keeps_error_class_without_message(
    client,
    exported_spans,
    monkeypatch,
):
    _enable_sensitive_capture(monkeypatch)
    client.raise_request_exception = False
    try:
        with override_settings(ROOT_URLCONF='tests.telemetry_urls'):
            response = client.get(
                f'{RAW_EVENT_PATH}?boom=1&note={NOTE}&email={EMAIL}',
                HTTP_AUTHORIZATION=f'Bearer {TOKEN}',
            )
    finally:
        _restore_capture()

    assert response.status_code == 500
    spans = exported_spans.get_finished_spans()
    _assert_secrets_absent(spans)
    server = [
        span
        for span in spans
        if span.attributes.get('http.route') == '/events/{event_id}/'
    ]
    assert len(server) == 1
    span = server[0]
    assert span.attributes['http.response.status_code'] == 500
    assert span.attributes['error.type'] == 'RuntimeError'
    assert span.attributes['app.outcome'] == 'server_error'
    assert span.status.description in (None, '')
    exception_events = [event for event in span.events if event.name == 'exception']
    assert exception_events
    for event in exception_events:
        assert event.attributes.get('exception.type') == 'RuntimeError'
        assert 'exception.message' not in (event.attributes or {})
        assert 'exception.stacktrace' not in (event.attributes or {})


@pytest.mark.django_db
def test_unmatched_path_span_name_has_no_raw_path(client, exported_spans):
    response = client.get(f'/no-such/{BABY}?note={NOTE}&email={EMAIL}')
    assert response.status_code == 404
    spans = exported_spans.get_finished_spans()
    _assert_secrets_absent(spans)
    server = [
        span
        for span in spans
        if span.attributes.get('http.request.method') == 'GET'
        and span.attributes.get('http.response.status_code') == 404
    ]
    assert server
    assert server[0].name == 'GET'
    assert 'http.route' not in server[0].attributes


class _GateExporter:
    """Stops forwarding after the test without shutting the processor down."""

    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.open = True

    def export(self, spans):
        if not self.open:
            from opentelemetry.sdk.trace.export import SpanExportResult

            return SpanExportResult.SUCCESS
        return self._delegate.export(spans)

    def shutdown(self) -> None:
        self.open = False

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class _OtlpCapture:
    """Collect OTLP/HTTP bodies exported while a block of spans is recorded."""

    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._gate: _GateExporter | None = None

    def finish(self) -> bytes:
        if self._gate is not None:
            self._gate.open = False
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return b''.join(self.bodies)


def _capture_otlp_export() -> _OtlpCapture:
    from opentelemetry import trace

    capture = _OtlpCapture()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0') or '0')
            capture.bodies.append(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    capture._server = server
    capture._thread = thread
    exporter = build_otlp_exporter(f'http://127.0.0.1:{port}/v1/traces')
    gate = _GateExporter(SanitizingSpanExporter(exporter))
    capture._gate = gate
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(gate))
    return capture


def _assert_payload_has_no_secrets(payload: bytes) -> None:
    assert payload, 'collector did not receive an export'
    for secret in SECRETS:
        assert secret.encode() not in payload
    assert b'/babies/' not in payload


def test_outbound_http_omits_lowercase_names_and_notes(exported_spans):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            length = int(self.headers.get('Content-Length', '0') or '0')
            if length:
                self.rfile.read(length)
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            self._reply()

        def do_POST(self):
            self._reply()

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    otlp_body = _capture_otlp_export()
    try:
        import urllib.request

        # Python 3.15's urllib treats userinfo as part of the host, so the
        # secret stays in the query string the instrumentor records.
        url = (
            f'http://127.0.0.1:{port}{RAW_OUTBOUND_PATH}'
            f'?note={NOTE}&email={EMAIL}&baby={BABY}'
            f'&access_token={TOKEN}&session={COOKIE}&import={IMPORT_ROW}'
            f'&caregiver={CAREGIVER}'
        )
        request = urllib.request.Request(
            url,
            data=f'note={NOTE}&caregiver={CAREGIVER}&import={IMPORT_ROW}'.encode(),
            headers={
                'Authorization': f'Bearer {TOKEN}',
                'Cookie': f'sessionid={COOKIE}',
                'X-Baby-Name': BABY,
            },
            method='POST',
        )
        with urllib.request.urlopen(request, timeout=2) as response:  # nosec B310
            assert response.status == 204
    finally:
        server.shutdown()
        server.server_close()
        payload = otlp_body.finish()

    _assert_payload_has_no_secrets(payload)

    spans = exported_spans.get_finished_spans()
    _assert_secrets_absent(spans)
    outbound = [
        span
        for span in spans
        if span.attributes.get('http.request.method') == 'POST'
        and span.attributes.get('http.response.status_code') == 204
    ]
    assert len(outbound) == 1
    span = outbound[0]
    assert span.name == 'POST'
    assert 'http.route' not in span.attributes
    assert span.start_time is not None
    assert span.end_time is not None
    assert span.end_time >= span.start_time
    assert span.status.description in (None, '')
    blob = _span_text([span])
    assert LOWER_NAME not in blob
    assert LOWER_NOTE not in blob
    assert LOWER_CAREGIVER not in blob
    assert '/babies/' not in blob


def test_exporter_drops_links_resource_events_and_sql_text():
    resource = Resource(
        {
            'service.name': 'baby-tracker',
            'caregiver.email': EMAIL,
            'process.command_args': ['--note', NOTE],
        }
    )
    provider = TracerProvider(resource=resource)
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(SanitizingSpanExporter(memory)))
    tracer = provider.get_tracer('telemetry-test')
    link_context = SpanContext(
        1,
        2,
        False,
        trace_state=TraceState([('vendor', NOTE)]),
    )
    unsafe_error = type(NOTE, (Exception,), {})
    lowercase_error = type(LOWER_NAME, (Exception,), {})
    with tracer.start_as_current_span(f'SELECT {NOTE}') as span:
        span.set_attribute('db.statement', f"SELECT '{NOTE}'")
        span.set_attribute('db.statement.parameters', f"['{NOTE}']")
        span.set_attribute('db.user', EMAIL)
        span.set_attribute('error.type', NOTE)
        span.set_attribute(
            'url.full',
            f'http://user:{TOKEN}@example.test{RAW_OUTBOUND_PATH}?note={NOTE}',
        )
        span.set_attribute('http.route', RAW_OUTBOUND_PATH)
        span.set_attribute('http.request.header.cookie', [f'sessionid={COOKIE}'])
        span.set_attribute('http.request.header.authorization', [f'Bearer {TOKEN}'])
        span.add_event('log', {'message': CAREGIVER, 'note': NOTE})
        span.record_exception(unsafe_error(f'{NOTE} in exception message'))
        span.record_exception(lowercase_error(LOWER_NOTE))
        span.record_exception(RuntimeError(f'{NOTE} in exception message'))
        span.set_status(Status(StatusCode.ERROR, f'{NOTE} in exception message'))
        span.add_link(
            link_context,
            attributes={'note': BABY, 'email': EMAIL},
        )

    spans = memory.get_finished_spans()
    assert len(spans) == 1
    _assert_secrets_absent(spans)
    span = spans[0]
    assert span.name == 'SELECT'
    assert span.attributes.get('db.system') == 'postgresql'
    assert span.attributes.get('db.operation') == 'SELECT'
    assert 'url.full' not in span.attributes
    assert span.resource.attributes['service.name'] == 'baby-tracker'
    assert 'caregiver.email' not in span.resource.attributes
    assert 'process.command_args' not in span.resource.attributes
    assert span.status.description in (None, '')
    assert span.links
    assert dict(span.links[0].attributes or {}) == {}
    assert len(span.links[0].context.trace_state) == 0
    assert [event.name for event in span.events] == ['exception']
    assert span.events[0].attributes.get('exception.type') == 'RuntimeError'
    assert span.attributes.get('error.type') == 'RuntimeError'
    assert 'http.route' not in span.attributes
    provider.shutdown()


def test_otlp_payload_omits_secrets_and_vendor_headers(monkeypatch):
    monkeypatch.setenv(
        'OTEL_EXPORTER_OTLP_HEADERS',
        'x-honeycomb-team=synthetic-vendor-key',
    )
    monkeypatch.setenv(
        'OTEL_EXPORTER_OTLP_TRACES_HEADERS',
        'x-honeycomb-dataset=synthetic-vendor-key',
    )
    received_headers: dict[str, str] = {}
    bodies: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0') or '0')
            received_headers.update(
                {key.lower(): value for key, value in self.headers.items()}
            )
            bodies.append(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    memory = InMemorySpanExporter()
    try:
        exporter = build_otlp_exporter(f'http://127.0.0.1:{port}/v1/traces')
        provider = TracerProvider(resource=safe_resource())
        provider.add_span_processor(SimpleSpanProcessor(SanitizingSpanExporter(memory)))
        provider.add_span_processor(
            SimpleSpanProcessor(SanitizingSpanExporter(exporter))
        )
        tracer = provider.get_tracer('opentelemetry.instrumentation.urllib')
        unsafe_error = type(NOTE, (Exception,), {})
        lowercase_error = type(LOWER_NAME, (Exception,), {})
        with tracer.start_as_current_span(
            f'POST {RAW_OUTBOUND_PATH}',
            kind=SpanKind.CLIENT,
        ) as span:
            span.set_attribute('http.request.method', 'POST')
            span.set_attribute('http.response.status_code', 204)
            span.set_attribute(
                'url.full', f'http://127.0.0.1{RAW_OUTBOUND_PATH}?note={NOTE}'
            )
            span.set_attribute('url.path', RAW_OUTBOUND_PATH)
            span.set_attribute('http.route', RAW_OUTBOUND_PATH)
            span.set_attribute('http.target', f'{RAW_OUTBOUND_PATH}?note={LOWER_NOTE}')
            span.set_attribute('error.type', NOTE)
            span.record_exception(unsafe_error(NOTE))
            span.record_exception(lowercase_error(LOWER_NOTE))
            span.record_exception(RuntimeError('boom'))
        provider.force_flush(3000)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    payload = b''.join(bodies)
    _assert_payload_has_no_secrets(payload)
    header_blob = str(received_headers)
    assert 'synthetic-vendor-key' not in header_blob
    assert 'honeycomb' not in header_blob.lower()
    spans = memory.get_finished_spans()
    assert len(spans) == 1
    _assert_secrets_absent(spans)
    exported = spans[0]
    assert exported.name == 'POST'
    assert exported.attributes.get('http.request.method') == 'POST'
    assert exported.attributes.get('http.response.status_code') == 204
    assert 'http.route' not in exported.attributes
    assert exported.attributes.get('error.type') == 'RuntimeError'
    assert exported.start_time is not None
    assert exported.end_time is not None
    assert exported.end_time >= exported.start_time
    event_types = [
        event.attributes.get('exception.type')
        for event in exported.events
        if event.name == 'exception'
    ]
    assert event_types == ['RuntimeError']


def test_unreachable_collector_does_not_break_requests(client, monkeypatch):
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://127.0.0.1:9')
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    exporter = build_otlp_exporter('http://127.0.0.1:9/v1/traces')
    processor = BatchSpanProcessor(
        SanitizingSpanExporter(exporter),
        schedule_delay_millis=50,
        export_timeout_millis=500,
    )
    provider = trace.get_tracer_provider()
    provider.add_span_processor(processor)

    class RaisingExporter:
        def export(self, spans):
            raise ConnectionError('collector unreachable')

        def shutdown(self):
            return None

        def force_flush(self, timeout_millis=30000):
            return True

    raising = SimpleSpanProcessor(SanitizingSpanExporter(RaisingExporter()))
    provider.add_span_processor(raising)
    try:
        response = client.get('/healthz')
        assert response.status_code == 200
        assert response.content == b'ok'
        flushed = processor.force_flush(2000)
        assert flushed in (True, False)
    finally:
        raising.shutdown()
        processor.shutdown()


def test_setup_failure_is_contained(monkeypatch):
    import config.telemetry as telemetry

    monkeypatch.setattr(telemetry, '_configured', False)

    def explode():
        raise RuntimeError('collector sdk missing')

    monkeypatch.setattr(telemetry, '_setup_telemetry', explode)
    assert setup_telemetry() is False


def test_healthz_is_answered_before_tracing(client, exported_spans):
    """Probes return before OpenTelemetry, which would reject a pod-IP Host."""
    response = client.get('/healthz', HTTP_HOST='10.9.8.7')
    assert response.status_code == 200
    assert response.content == b'ok'
    spans = exported_spans.get_finished_spans()
    assert '10.9.8.7' not in _span_text(spans)
    assert not any(span.attributes.get('http.route') == '/healthz' for span in spans)
