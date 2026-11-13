"""Server-rendered pages. Authorization is checked again in each view."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from config.telemetry import record_functional_attributes
from tracker.access import (
    accept_invitation,
    bind_session,
    clear_login_failures,
    create_invitation,
    drop_tracked_session,
    invitation_from_token,
    lockout_status,
    record_login_failure,
    revoke_other_sessions,
    sign_out_everywhere,
    update_membership,
)
from tracker.activity import SUMMARY_LABELS, activity_board, sheet, summary_range
from tracker.bottles import create_bottle
from tracker.bottles import log_save_failure as log_bottle_failure
from tracker.clock import ensure_aware, reject_if_too_far_future
from tracker.diapers import create_diaper, log_save_failure
from tracker.exceptions import DeletedRecord, VersionConflict
from tracker.extra_models import ImportBatch, Membership, Role
from tracker.feeds import (
    apply_timer,
    create_manual_feed,
    current_side,
    display_totals,
    log_timer_failure,
    open_feed,
    update_feed_totals,
)
from tracker.models import Event
from tracker.nara import (
    RollbackNeedsDecision,
    abandon,
    commit_batch,
    rollback_batch,
    rollback_preview,
    set_decisions,
    set_name_map,
    stage_upload,
    stored_bytes,
)
from tracker.policy import (
    ARCHIVED_KINDS,
    can_admin,
    can_create,
    can_delete_event,
    can_edit_event,
    can_import,
    can_restore_event,
)
from tracker.quicklog import create_pump, create_sleep, end_after
from tracker.quicklog import log_save_failure as log_quick_failure
from tracker.summary import (
    RANGE_LABELS,
    RANGE_LINKS,
    change_marker,
    day_feed_totals,
    diaper_counts,
    events_in_range,
    events_on_day,
    last_side_used,
    latest_diaper,
    latest_feed,
    range_summary,
    time_since,
)
from tracker.trends import TREND_LENGTHS, build_trends, empty_trends, trend_length


def _role(request) -> str:
    membership = getattr(request, 'membership', None)
    if membership is None:
        return 'anonymous'
    return membership.role


def _require(request):
    if not request.user.is_authenticated:
        if request.headers.get('HX-Request'):
            response = HttpResponse(status=401)
            response['HX-Redirect'] = reverse('login')
            return response
        nxt = request.get_full_path()
        return redirect(f'/login/?next={nxt}')
    if request.membership is None:
        return HttpResponseForbidden('You cannot do that.')
    return None


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith('/') or value.startswith('//'):
        return reverse('home')
    return value


def _baby(request):
    babies = list(request.household.babies.all())
    if not babies:
        return None
    selected = request.session.get('baby_id')
    for baby in babies:
        if str(baby.pk) == str(selected):
            return baby
    request.session['baby_id'] = str(babies[0].pk)
    return babies[0]


def _event_for(request, event_id, *, allow_deleted=False):
    manager = Event.all_objects if allow_deleted else Event.objects
    event = manager.filter(pk=event_id, household=request.household).first()
    if event is None:
        raise Http404()
    return event


def _parse_when(request):
    raw = (request.POST.get('occurred_at') or '').strip()
    zone = ZoneInfo(request.household.timezone)
    if not raw:
        return timezone.now()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError({'occurred_at': 'Enter a valid time.'}) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return ensure_aware(parsed, 'occurred_at')


def _minutes_to_seconds(raw, field):
    text = (raw or '').strip()
    if text == '':
        return None
    try:
        minutes = float(text)
    except ValueError as exc:
        raise ValidationError({field: 'Enter a number of minutes.'}) from exc
    return int(round(minutes * 60))


def _activity_context(request):
    baby = _baby(request)
    board = activity_board(baby) if baby is not None else None
    return {
        'board': board,
        'baby': baby,
        'babies': list(request.household.babies.all()),
        'marker': change_marker(baby) if baby else '0',
    }


def home(request):
    if not (request.user.is_authenticated and request.membership is not None):
        return redirect('login')
    context = _activity_context(request)
    if context['board'] is None:
        return render(request, 'tracker/activity.html', context)
    context.update(context['board'])
    return render(request, 'tracker/activity.html', context)


@require_GET
def activity_partial(request):
    denied = _require(request)
    if denied:
        return denied
    context = _activity_context(request)
    if context['board'] is not None:
        context.update(context['board'])
    return render(request, 'tracker/activity_cards.html', context)


@require_GET
def activity_summary(request):
    denied = _require(request)
    if denied:
        return denied
    baby = _baby(request)
    key = summary_range(request.GET.get('range'))
    summary = sheet(baby, key) if baby is not None else None
    return render(
        request,
        'tracker/summary_sheet.html',
        {
            'summary': summary,
            'summary_choices': tuple(SUMMARY_LABELS.items()),
        },
    )


@require_GET
def guides(request):
    denied = _require(request)
    if denied:
        return denied
    return render(request, 'tracker/guides.html')


def healthz(request):
    return HttpResponse('ok', content_type='text/plain')


def readyz(request):
    from tracker.health import readyz as probe

    return probe(request)


@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated and request.membership is not None:
        return redirect('home')
    error = ''
    status = 200
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        if lockout_status(username):
            error = 'Too many attempts. Try again later.'
            status = 429
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                record_login_failure(username)
                error = 'Those credentials were not accepted.'
                status = 400
            else:
                membership = (
                    Membership.objects.filter(user=user, is_active=True)
                    .select_related('household')
                    .first()
                )
                if membership is None:
                    error = 'This account is deactivated.'
                    status = 403
                else:
                    login(request, user)
                    bind_session(request, user, membership)
                    clear_login_failures(username)
                    record_functional_attributes(
                        event_type='account',
                        outcome='success',
                        role=membership.role,
                    )
                    return redirect(_safe_next(request.POST.get('next')))
        record_functional_attributes(event_type='account', outcome='failure')
    return render(
        request,
        'tracker/login.html',
        {
            'error': error,
            'next': _safe_next(request.GET.get('next') or request.POST.get('next')),
        },
        status=status,
    )


@require_POST
def logout_view(request):
    session_key = request.session.session_key
    logout(request)
    drop_tracked_session(session_key)
    return redirect('login')


def signup_closed(_request):
    raise Http404()


@require_http_methods(['GET', 'POST'])
def accept_invite(request, token):
    invitation = invitation_from_token(token)
    problem = ''
    if invitation is None:
        problem = 'This invitation link is not valid.'
    elif invitation.accepted_at is not None:
        problem = 'This invitation was already used.'
    elif invitation.expires_at <= timezone.now():
        problem = 'This invitation has expired.'
    if request.method == 'POST' and not problem:
        password = request.POST.get('new_password1') or ''
        confirm = request.POST.get('new_password2') or ''
        if password != confirm:
            problem = 'The two passwords did not match.'
        else:
            try:
                validate_password(password)
                user, membership = accept_invitation(token=token, password=password)
            except ValidationError as exc:
                problem = ' '.join(exc.messages)
            else:
                login(request, user)
                bind_session(request, user, membership)
                record_functional_attributes(
                    event_type='invite',
                    outcome='success',
                    role=membership.role,
                )
                return redirect('timeline')
    elif request.method == 'POST':
        record_functional_attributes(event_type='invite', outcome='failure')
    return render(
        request,
        'tracker/invite.html',
        {'problem': problem, 'invitation': invitation, 'token': token},
    )


def _day_from(request, day_text=None):
    if day_text:
        try:
            return date.fromisoformat(day_text)
        except ValueError as exc:
            raise Http404() from exc
    zone = ZoneInfo(request.household.timezone)
    return timezone.now().astimezone(zone).date()


def _range_key(request) -> str:
    key = request.GET.get('range') or '24h'
    if key not in RANGE_LABELS:
        return '24h'
    return key


def _range_context(request, key):
    baby = _baby(request)
    now = timezone.now()
    events = list(events_in_range(baby, key, now=now)) if baby else []
    active = open_feed(baby) if baby else None
    last_diaper = latest_diaper(baby) if baby else None
    last_feed = latest_feed(baby) if baby else None
    return {
        'range_mode': True,
        'range_key': key,
        'range_label': RANGE_LABELS[key],
        'range_choices': RANGE_LINKS,
        'baby': baby,
        'babies': list(request.household.babies.all()),
        'events': events,
        'summary': range_summary(events),
        'active_feed': active,
        'active_side': current_side(active) if active else '',
        'last_side': last_side_used(baby) if baby else '',
        'since_diaper': time_since(last_diaper.occurred_at, now=now)
        if last_diaper
        else None,
        'since_feed_start': time_since(last_feed.occurred_at, now=now)
        if last_feed
        else None,
        'since_feed_end': (
            time_since(last_feed.feed_ended_at, now=now)
            if last_feed and last_feed.feed_ended_at
            else None
        ),
        'marker': change_marker(baby) if baby else '0',
    }


def _timeline_context(request, day):
    baby = _baby(request)
    kind = request.GET.get('type') or ''
    if kind not in ('', Event.Kind.DIAPER, Event.Kind.FEED):
        kind = ''
    events = []
    if baby is not None:
        events = list(events_on_day(baby, day, kind=kind or None))
    today = timezone.now().astimezone(ZoneInfo(request.household.timezone)).date()
    today_events = list(events_on_day(baby, today)) if baby else []
    left, right, feed_count = day_feed_totals(today_events)
    active = open_feed(baby) if baby else None
    last_diaper = latest_diaper(baby) if baby else None
    last_feed = latest_feed(baby) if baby else None
    return {
        'baby': baby,
        'babies': list(request.household.babies.all()),
        'day': day,
        'previous_day': day - timedelta(days=1),
        'next_day': day + timedelta(days=1),
        'kind': kind,
        'events': events,
        'diaper_counts': diaper_counts(events),
        'today_left': left,
        'today_right': right,
        'today_feed_count': feed_count,
        'active_feed': active,
        'active_side': current_side(active) if active else '',
        'last_side': last_side_used(baby) if baby else '',
        'since_diaper': time_since(last_diaper.occurred_at) if last_diaper else None,
        'since_feed_start': time_since(last_feed.occurred_at) if last_feed else None,
        'since_feed_end': (
            time_since(last_feed.feed_ended_at)
            if last_feed and last_feed.feed_ended_at
            else None
        ),
        'marker': change_marker(baby) if baby else '0',
    }


def _trends_context(request):
    days = trend_length(request.GET.get('days'))
    baby = _baby(request)
    if baby is None:
        report = empty_trends(request.household, days)
    else:
        report = build_trends(baby, days)
    report['lengths'] = TREND_LENGTHS
    return report


@require_GET
def trends(request):
    denied = _require(request)
    if denied:
        return denied
    return render(request, 'tracker/trends.html', _trends_context(request))


@require_GET
def trends_partial(request):
    denied = _require(request)
    if denied:
        return denied
    return render(request, 'tracker/trends_panel.html', _trends_context(request))


@require_GET
def timeline(request, day=None):
    denied = _require(request)
    if denied:
        return denied
    if day is None:
        return render(
            request,
            'tracker/timeline.html',
            _range_context(request, _range_key(request)),
        )
    try:
        selected = _day_from(request, day)
    except Http404:
        raise
    context = _timeline_context(request, selected)
    context['range_mode'] = False
    return render(request, 'tracker/timeline.html', context)


@require_GET
def timeline_partial(request):
    denied = _require(request)
    if denied:
        return denied
    day_text = request.GET.get('day')
    if not day_text:
        return render(
            request,
            'tracker/range_panel.html',
            _range_context(request, _range_key(request)),
        )
    try:
        selected = _day_from(request, day_text)
    except Http404:
        selected = _day_from(request, None)
    context = _timeline_context(request, selected)
    return render(request, 'tracker/timeline_partial.html', context)


@require_GET
def timeline_marker(request):
    denied = _require(request)
    if denied:
        return denied
    baby = _baby(request)
    marker = change_marker(baby) if baby else '0'
    if request.headers.get('If-None-Match') == marker:
        response = HttpResponse(status=304)
        response['ETag'] = marker
        response['Cache-Control'] = 'no-store'
        return response
    response = JsonResponse({'marker': marker})
    response['ETag'] = marker
    response['Cache-Control'] = 'no-store'
    return response


@require_POST
def select_baby(request):
    denied = _require(request)
    if denied:
        return denied
    baby_id = request.POST.get('baby_id')
    if request.household.babies.filter(pk=baby_id).exists():
        request.session['baby_id'] = baby_id
    return redirect('timeline')


@require_POST
def bottle_create(request):
    denied = _require(request)
    if denied:
        return denied
    if not can_create(request.membership):
        return HttpResponseForbidden('You cannot do that.')
    baby = _baby(request)
    if baby is None:
        return HttpResponseForbidden('You cannot do that.')
    record_functional_attributes(event_type='bottle', role=_role(request))
    try:
        _event, replay = create_bottle(
            actor=request.user,
            baby=baby,
            bottle_type=request.POST.get('bottle_type') or '',
            volume=request.POST.get('volume') or '',
            unit=request.POST.get('unit') or '',
            occurred_at=_parse_when(request),
            idempotency_key=request.POST.get('idempotency_key') or '',
        )
    except ValidationError as exc:
        record_functional_attributes(event_type='bottle', outcome='failure')
        return render(
            request,
            'tracker/save_result.html',
            {'error': ' '.join(exc.messages), 'saved': False},
            status=400,
        )
    except Exception:
        log_bottle_failure()
        record_functional_attributes(event_type='bottle', outcome='save_failed')
        return render(
            request,
            'tracker/save_result.html',
            {'saved': False},
            status=500,
        )
    record_functional_attributes(event_type='bottle', outcome='success')
    response = render(
        request,
        'tracker/save_result.html',
        {'saved': True, 'replay': replay},
    )
    response['HX-Trigger'] = 'refresh-timeline'
    return response


@require_POST
def pump_create(request):
    denied = _require(request)
    if denied:
        return denied
    if not can_create(request.membership):
        return HttpResponseForbidden('You cannot do that.')
    baby = _baby(request)
    if baby is None:
        return HttpResponseForbidden('You cannot do that.')
    record_functional_attributes(event_type='pump', role=_role(request))
    try:
        _event, replay = create_pump(
            actor=request.user,
            baby=baby,
            volume=request.POST.get('volume') or '',
            occurred_at=_parse_when(request),
            idempotency_key=request.POST.get('idempotency_key') or '',
        )
    except ValidationError as exc:
        record_functional_attributes(event_type='pump', outcome='failure')
        return render(
            request,
            'tracker/save_result.html',
            {'error': ' '.join(exc.messages), 'saved': False},
            status=400,
        )
    except Exception:
        log_quick_failure('pump')
        record_functional_attributes(event_type='pump', outcome='save_failed')
        return render(request, 'tracker/save_result.html', {'saved': False}, status=500)
    record_functional_attributes(event_type='pump', outcome='success')
    response = render(
        request,
        'tracker/save_result.html',
        {'saved': True, 'replay': replay},
    )
    response['HX-Trigger'] = 'refresh-timeline'
    return response


@require_POST
def sleep_create(request):
    denied = _require(request)
    if denied:
        return denied
    if not can_create(request.membership):
        return HttpResponseForbidden('You cannot do that.')
    baby = _baby(request)
    if baby is None:
        return HttpResponseForbidden('You cannot do that.')
    record_functional_attributes(event_type='sleep', role=_role(request))
    try:
        start = _parse_when(request)
        _event, replay = create_sleep(
            actor=request.user,
            baby=baby,
            occurred_at=start,
            ended_at=end_after(start, request.POST.get('minutes') or ''),
            notes='',
            idempotency_key=request.POST.get('idempotency_key') or '',
        )
    except ValidationError as exc:
        record_functional_attributes(event_type='sleep', outcome='failure')
        return render(
            request,
            'tracker/save_result.html',
            {'error': ' '.join(exc.messages), 'saved': False},
            status=400,
        )
    except Exception:
        log_quick_failure('sleep')
        record_functional_attributes(event_type='sleep', outcome='save_failed')
        return render(request, 'tracker/save_result.html', {'saved': False}, status=500)
    record_functional_attributes(event_type='sleep', outcome='success')
    response = render(
        request,
        'tracker/save_result.html',
        {'saved': True, 'replay': replay},
    )
    response['HX-Trigger'] = 'refresh-timeline'
    return response


@require_POST
def diaper_create(request):
    denied = _require(request)
    if denied:
        return denied
    if not can_create(request.membership):
        return HttpResponseForbidden('You cannot do that.')
    baby = _baby(request)
    if baby is None:
        return HttpResponseForbidden('You cannot do that.')
    record_functional_attributes(
        event_type='diaper',
        role=_role(request),
    )
    try:
        event, replay = create_diaper(
            actor=request.user,
            baby=baby,
            diaper_type=request.POST.get('diaper_type') or '',
            occurred_at=_parse_when(request),
            notes=request.POST.get('notes') or '',
            idempotency_key=request.POST.get('idempotency_key') or '',
        )
    except ValidationError as exc:
        record_functional_attributes(event_type='diaper', outcome='failure')
        return render(
            request,
            'tracker/save_result.html',
            {'error': ' '.join(exc.messages), 'saved': False},
            status=400,
        )
    except Exception:
        log_save_failure()
        record_functional_attributes(event_type='diaper', outcome='save_failed')
        return render(
            request,
            'tracker/save_result.html',
            {'error': 'Not saved', 'saved': False},
            status=500,
        )
    record_functional_attributes(
        event_type=event.diaper_type or 'diaper',
        outcome='success',
    )
    response = render(
        request,
        'tracker/save_result.html',
        {'saved': True, 'event': event, 'replay': replay},
    )
    response['HX-Trigger'] = 'refresh-timeline'
    return response


@require_http_methods(['GET', 'POST'])
def event_edit(request, event_id):
    denied = _require(request)
    if denied:
        return denied
    event = _event_for(request, event_id, allow_deleted=True)
    if event.deleted_at is not None:
        raise Http404()
    if not can_edit_event(request.membership, event):
        record_functional_attributes(outcome='failure', role=_role(request))
        return HttpResponseForbidden('You cannot do that.')
    error = ''
    status = 200
    if request.method == 'POST':
        try:
            expected = int(request.POST.get('version') or '0')
        except ValueError:
            expected = -1
        try:
            if event.kind == Event.Kind.FEED:
                update_feed_totals(
                    actor=request.user,
                    event=event,
                    expected_version=expected,
                    left_seconds=_minutes_to_seconds(
                        request.POST.get('left_minutes'),
                        'left_minutes',
                    ),
                    right_seconds=_minutes_to_seconds(
                        request.POST.get('right_minutes'),
                        'right_minutes',
                    ),
                    notes=request.POST.get('notes') or '',
                    occurred_at=_parse_when(request),
                )
            elif event.kind == Event.Kind.DIAPER:
                event.diaper_type = request.POST.get('diaper_type') or ''
                event.notes = request.POST.get('notes') or ''
                event.occurred_at = _parse_when(request)
                if event.diaper_type not in Event.DiaperType.values:
                    raise ValidationError(
                        {'diaper_type': 'Choose wet, dirty, both, or dry.'}
                    )
                reject_if_too_far_future(event.occurred_at)
                if len(event.notes) > 2000:
                    raise ValidationError(
                        {'notes': 'Notes are limited to 2000 characters.'}
                    )
                event.save(actor=request.user, expected_version=expected)
            else:
                event.notes = request.POST.get('notes') or ''
                event.occurred_at = _parse_when(request)
                event.save(actor=request.user, expected_version=expected)
        except VersionConflict:
            event.refresh_from_db()
            record_functional_attributes(outcome='conflict', role=_role(request))
            return render(
                request,
                'tracker/event_form.html',
                {'event': event, 'conflict': True},
                status=409,
            )
        except (ValidationError, DeletedRecord) as exc:
            error = ' '.join(getattr(exc, 'messages', []) or [str(exc)])
            status = 400
        else:
            record_functional_attributes(outcome='success', role=_role(request))
            return redirect('timeline')
    return render(
        request,
        'tracker/event_form.html',
        {'event': event, 'conflict': False, 'error': error},
        status=status,
    )


@require_POST
def event_delete(request, event_id):
    denied = _require(request)
    if denied:
        return denied
    event = _event_for(request, event_id)
    if not can_delete_event(request.membership, event):
        return HttpResponseForbidden('You cannot do that.')
    try:
        expected = int(request.POST.get('version') or event.version)
        event.delete(actor=request.user, expected_version=expected)
    except VersionConflict:
        event.refresh_from_db()
        return render(
            request,
            'tracker/event_form.html',
            {'event': event, 'conflict': True},
            status=409,
        )
    return redirect('timeline')


@require_POST
def event_restore(request, event_id):
    denied = _require(request)
    if denied:
        return denied
    event = _event_for(request, event_id, allow_deleted=True)
    if not can_restore_event(request.membership, event):
        return HttpResponseForbidden('You cannot do that.')
    try:
        event.restore(actor=request.user, expected_version=event.version)
    except VersionConflict:
        return HttpResponse('This entry changed. Reload and try again.', status=409)
    return redirect('timeline')


@require_GET
def event_history(request, event_id):
    denied = _require(request)
    if denied:
        return denied
    event = _event_for(request, event_id, allow_deleted=True)
    if not can_edit_event(request.membership, event) and not can_admin(
        request.membership
    ):
        if request.membership.role == Role.CAREGIVER and event.created_by_id != (
            request.user.pk
        ):
            return HttpResponseForbidden('You cannot do that.')
    entries = list(event.history().select_related('actor'))
    return render(
        request,
        'tracker/history.html',
        {'event': event, 'entries': entries},
    )


def _feed_payload(session):
    now = timezone.now()
    left, right = display_totals(session, now=now) if session else (0, 0)
    segments = []
    if session is not None:
        for segment in session.segments.order_by('started_at'):
            segments.append(
                {
                    'side': segment.side,
                    'started_at': segment.started_at.isoformat(),
                    'ended_at': (
                        segment.ended_at.isoformat() if segment.ended_at else None
                    ),
                }
            )
    return {
        'server_now': now.isoformat(),
        'state': session.feed_state if session else '',
        'version': session.version if session else 0,
        'session_id': str(session.pk) if session else '',
        'side': current_side(session, now=now) if session else '',
        'left_seconds': left,
        'right_seconds': right,
        'segments': segments,
        'source': session.feed_source if session else '',
    }


@require_GET
def feed_page(request):
    denied = _require(request)
    if denied:
        return denied
    baby = _baby(request)
    session = open_feed(baby) if baby else None
    return render(
        request,
        'tracker/feed.html',
        {'baby': baby, 'session': session, 'payload': _feed_payload(session)},
    )


@require_GET
def feed_state(request):
    denied = _require(request)
    if denied:
        return denied
    baby = _baby(request)
    session = open_feed(baby) if baby else None
    response = JsonResponse(_feed_payload(session))
    response['Cache-Control'] = 'no-store'
    return response


@require_POST
def feed_act(request):
    denied = _require(request)
    if denied:
        return denied
    if not can_create(request.membership):
        return HttpResponseForbidden('You cannot do that.')
    baby = _baby(request)
    if baby is None:
        return HttpResponseForbidden('You cannot do that.')
    record_functional_attributes(event_type='feed', role=_role(request))
    try:
        raw_version = request.POST.get('version')
        expected = int(raw_version) if raw_version not in (None, '') else None
        session, replay = apply_timer(
            actor=request.user,
            baby=baby,
            action=request.POST.get('action') or '',
            side=request.POST.get('side') or '',
            idempotency_key=request.POST.get('idempotency_key') or '',
            expected_version=expected,
        )
    except VersionConflict:
        session = open_feed(baby)
        record_functional_attributes(event_type='feed', outcome='conflict')
        response = JsonResponse(_feed_payload(session), status=409)
        response['Cache-Control'] = 'no-store'
        return response
    except ValidationError as exc:
        record_functional_attributes(event_type='feed', outcome='failure')
        return JsonResponse(
            {'error': ' '.join(exc.messages), 'state': _feed_payload(open_feed(baby))},
            status=400,
        )
    except Exception:
        log_timer_failure()
        record_functional_attributes(event_type='feed', outcome='save_failed')
        return JsonResponse(
            {'error': 'Not applied', 'state': _feed_payload(open_feed(baby))},
            status=500,
        )
    record_functional_attributes(event_type='feed', outcome='success')
    payload = _feed_payload(session)
    payload['replay'] = replay
    if request.headers.get('HX-Request'):
        response = render(
            request,
            'tracker/feed_panel.html',
            {'baby': baby, 'session': session, 'payload': payload},
        )
        response['HX-Trigger'] = 'refresh-timeline'
        return response
    return JsonResponse(payload)


@require_http_methods(['GET', 'POST'])
def feed_manual(request):
    denied = _require(request)
    if denied:
        return denied
    if not can_create(request.membership):
        return HttpResponseForbidden('You cannot do that.')
    error = ''
    status = 200
    if request.method == 'POST':
        baby = _baby(request)
        try:
            create_manual_feed(
                actor=request.user,
                baby=baby,
                occurred_at=_parse_when(request),
                left_seconds=_minutes_to_seconds(
                    request.POST.get('left_minutes'),
                    'left_minutes',
                ),
                right_seconds=_minutes_to_seconds(
                    request.POST.get('right_minutes'),
                    'right_minutes',
                ),
                start_side=request.POST.get('start_side') or '',
                end_side=request.POST.get('end_side') or '',
                notes=request.POST.get('notes') or '',
                idempotency_key=request.POST.get('idempotency_key') or '',
            )
        except ValidationError as exc:
            error = ' '.join(exc.messages)
            status = 400
            record_functional_attributes(event_type='feed', outcome='failure')
        else:
            record_functional_attributes(event_type='feed', outcome='success')
            return redirect('timeline')
    return render(
        request,
        'tracker/feed_manual.html',
        {'error': error},
        status=status,
    )


@require_http_methods(['GET', 'POST'])
def password_change(request):
    denied = _require(request)
    if denied:
        return denied
    form = PasswordChangeForm(user=request.user, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        bind_session(request, user, request.membership)
        return redirect('timeline')
    return render(request, 'tracker/password.html', {'form': form})


@require_GET
def session_list(request):
    denied = _require(request)
    if denied:
        return denied
    from tracker.extra_models import TrackedSession

    rows = TrackedSession.objects.filter(user=request.user).order_by('-created_at')
    return render(
        request,
        'tracker/sessions.html',
        {'rows': rows, 'current': request.session.session_key},
    )


@require_POST
def revoke_other_sessions_view(request):
    denied = _require(request)
    if denied:
        return denied
    revoke_other_sessions(
        request.user,
        keep_session_key=request.session.session_key,
        actor=request.user,
        membership=request.membership,
    )
    return redirect('session-list')


@require_GET
def archive(request):
    denied = _require(request)
    if denied:
        return denied
    baby = _baby(request)
    rows = []
    if baby is not None:
        rows = Event.objects.filter(baby=baby, kind__in=ARCHIVED_KINDS).order_by(
            '-occurred_at'
        )
    return render(request, 'tracker/archive.html', {'rows': rows, 'baby': baby})


def _import_or_deny(request):
    denied = _require(request)
    if denied:
        return denied, None
    if not can_import(request.membership):
        return HttpResponseForbidden('You cannot do that.'), None
    return None, _baby(request)


@require_GET
def import_home(request):
    denied, baby = _import_or_deny(request)
    if denied:
        return denied
    batches = ImportBatch.objects.filter(household=request.household)[:20]
    return render(
        request,
        'tracker/import_home.html',
        {'batches': batches, 'baby': baby},
    )


@require_POST
def import_upload(request):
    denied, baby = _import_or_deny(request)
    if denied:
        return denied
    upload = request.FILES.get('export')
    if upload is None or baby is None:
        return render(
            request,
            'tracker/import_home.html',
            {'error': 'Choose a CSV file.', 'baby': baby, 'batches': []},
            status=400,
        )
    from django.conf import settings

    if upload.size > settings.IMPORT_MAX_BYTES:
        return HttpResponse('That file is too large.', status=400)
    try:
        text = upload.read().decode('utf-8')
    except UnicodeDecodeError:
        record_functional_attributes(event_type='import', outcome='failure')
        return HttpResponse('The file could not be read.', status=400)
    upload.seek(0)
    try:
        batch = stage_upload(
            actor=request.user,
            household=request.household,
            baby=baby,
            uploaded=upload,
            text=text,
        )
    except Exception:
        record_functional_attributes(event_type='import', outcome='save_failed')
        return HttpResponse('Not saved', status=500)
    record_functional_attributes(event_type='import', outcome='success')
    return redirect('import-preview', batch_id=batch.pk)


@require_http_methods(['GET', 'POST'])
def import_preview(request, batch_id):
    denied, _baby_obj = _import_or_deny(request)
    if denied:
        return denied
    batch = ImportBatch.objects.filter(
        pk=batch_id,
        household=request.household,
    ).first()
    if batch is None:
        raise Http404()
    if request.method == 'POST':
        decisions = {}
        name_map = {}
        for row in batch.rows.all():
            decisions[str(row.pk)] = request.POST.get(f'decision-{row.pk}', 'keep')
            name = (row.mapped or {}).get('caregiver_name') or ''
            if name:
                name_map[name] = request.POST.get(f'map-{name}', '')
        set_decisions(batch, decisions)
        member_ids = {
            str(item.user_id)
            for item in request.household.memberships.filter(is_active=True)
        }
        safe_map = {
            name: (user_id if user_id in member_ids else '')
            for name, user_id in name_map.items()
        }
        set_name_map(batch, safe_map)
        return redirect('import-preview', batch_id=batch.pk)
    members = request.household.memberships.filter(is_active=True).select_related(
        'user'
    )
    names = sorted(
        {
            (row.mapped or {}).get('caregiver_name') or ''
            for row in batch.rows.all()
            if (row.mapped or {}).get('caregiver_name')
        }
    )
    return render(
        request,
        'tracker/import_preview.html',
        {
            'batch': batch,
            'rows': batch.rows.order_by('record_type', 'row_number'),
            'members': members,
            'names': names,
        },
    )


@require_POST
def import_commit(request, batch_id):
    denied, _baby_obj = _import_or_deny(request)
    if denied:
        return denied
    batch = ImportBatch.objects.filter(
        pk=batch_id,
        household=request.household,
    ).first()
    if batch is None:
        raise Http404()
    skip = request.POST.get('skip_errors') == '1'
    try:
        commit_batch(batch, actor=request.user, skip_errors=skip)
    except ValidationError as exc:
        record_functional_attributes(event_type='import', outcome='failure')
        return render(
            request,
            'tracker/import_preview.html',
            {
                'batch': batch,
                'rows': batch.rows.order_by('record_type', 'row_number'),
                'members': request.household.memberships.filter(is_active=True),
                'names': [],
                'error': ' '.join(exc.messages),
            },
            status=400,
        )
    record_functional_attributes(event_type='import', outcome='success')
    return redirect('timeline')


@require_POST
def import_abandon(request, batch_id):
    denied, _baby_obj = _import_or_deny(request)
    if denied:
        return denied
    batch = ImportBatch.objects.filter(
        pk=batch_id,
        household=request.household,
    ).first()
    if batch is None:
        raise Http404()
    abandon(batch, actor=request.user)
    return redirect('import-home')


@require_http_methods(['GET', 'POST'])
def import_rollback(request, batch_id):
    denied, _baby_obj = _import_or_deny(request)
    if denied:
        return denied
    batch = ImportBatch.all_objects.filter(
        pk=batch_id,
        household=request.household,
    ).first()
    if batch is None:
        raise Http404()
    edited = rollback_preview(batch)
    error = ''
    if request.method == 'POST':
        decisions = {
            key.removeprefix('choice-'): request.POST.get(key)
            for key in request.POST
            if key.startswith('choice-')
        }
        try:
            rollback_batch(batch, actor=request.user, decisions=decisions)
        except RollbackNeedsDecision as exc:
            edited = exc.events
            error = 'Choose keep or remove for each edited record.'
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        else:
            return redirect('import-home')
    return render(
        request,
        'tracker/import_rollback.html',
        {'batch': batch, 'edited': edited, 'error': error},
    )


@require_GET
def import_file(request, batch_id):
    denied, _baby_obj = _import_or_deny(request)
    if denied:
        return denied
    batch = ImportBatch.all_objects.filter(
        pk=batch_id,
        household=request.household,
    ).first()
    if batch is None:
        raise Http404()
    payload = stored_bytes(batch)
    if payload is None:
        raise Http404()
    response = HttpResponse(payload, content_type='text/csv')
    response['Content-Disposition'] = 'attachment'
    response['Cache-Control'] = 'no-store'
    return response


@require_http_methods(['GET', 'POST'])
def portal(request):
    denied = _require(request)
    if denied:
        return denied
    if not can_admin(request.membership):
        return HttpResponseForbidden('You cannot do that.')
    invite_link = ''
    error = ''
    if request.method == 'POST' and request.POST.get('form') == 'invite':
        try:
            _invitation, token = create_invitation(
                actor=request.user,
                household=request.household,
                email=request.POST.get('email') or '',
                role=request.POST.get('role') or '',
            )
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        else:
            invite_link = request.build_absolute_uri(
                reverse('accept-invite', args=[token])
            )
            record_functional_attributes(event_type='invite', outcome='success')
    if request.method == 'POST' and request.POST.get('form') == 'timezone':
        zone_name = (request.POST.get('timezone') or '').strip()
        request.household.timezone = zone_name
        try:
            request.household.save(
                actor=request.user,
                expected_version=request.household.version,
            )
        except ValidationError as exc:
            error = ' '.join(exc.messages)
    members = (
        Membership.objects.filter(household=request.household)
        .select_related('user')
        .order_by('created_at')
    )
    return render(
        request,
        'tracker/portal.html',
        {
            'members': members,
            'invite_link': invite_link,
            'error': error,
            'roles': Role.choices,
        },
    )


@require_POST
def portal_member(request, membership_id):
    denied = _require(request)
    if denied:
        return denied
    if not can_admin(request.membership):
        return HttpResponseForbidden('You cannot do that.')
    membership = Membership.objects.filter(
        pk=membership_id,
        household=request.household,
    ).first()
    if membership is None:
        raise Http404()
    action = request.POST.get('action') or 'save'
    try:
        if action == 'sign-out':
            sign_out_everywhere(actor=request.user, membership=membership)
        else:
            update_membership(
                actor=request.user,
                membership=membership,
                role=request.POST.get('role') or membership.role,
                is_active=request.POST.get('is_active') == '1',
            )
    except ValidationError as exc:
        return render(
            request,
            'tracker/portal.html',
            {
                'members': Membership.objects.filter(household=request.household),
                'error': ' '.join(exc.messages),
                'roles': Role.choices,
                'invite_link': '',
            },
            status=400,
        )
    if request.user.pk == membership.user_id and action == 'sign-out':
        return redirect('login')
    return redirect('portal')


@require_GET
def manifest(request):
    payload = {
        'name': 'Baby Tracker',
        'short_name': 'Baby',
        'start_url': '/timeline/',
        'scope': '/',
        'display': 'standalone',
        'background_color': '#f6f1e7',
        'theme_color': '#1f6f6a',
        'icons': [
            {
                'src': '/static/icons/icon-192.png',
                'sizes': '192x192',
                'type': 'image/png',
                'purpose': 'any',
            },
            {
                'src': '/static/icons/icon-512.png',
                'sizes': '512x512',
                'type': 'image/png',
                'purpose': 'any',
            },
        ],
    }
    return JsonResponse(payload, content_type='application/manifest+json')


@require_GET
def service_worker(request):
    script = """
const SHELL = [
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/js/htmx.min.js',
  '/static/icons/icon-192.png',
];
self.addEventListener('install', (event) => {
  event.waitUntil(caches.open('shell-v2').then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== 'shell-v2').map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') {
    return;
  }
  const url = new URL(event.request.url);
  if (SHELL.indexOf(url.pathname) === -1) {
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
""".strip()
    response = HttpResponse(script, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response
