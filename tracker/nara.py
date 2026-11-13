"""Nara Baby CSV parser, staging, commit, and rollback.

Fixtures in tests are synthetic. This module does not log row contents.
Real exports stay out of the repository.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone as dj_timezone

from tracker.audit import snapshot
from tracker.extra_models import ImportBatch, ImportLink, ImportRow
from tracker.models import Event

logger = logging.getLogger(__name__)

SOURCE = 'nara'
EPOCH_TOLERANCE_SECONDS = 90

SHARED_COLUMNS = (
    'Type',
    'Profile Name',
    'Start Date/time',
    'Start Date/time (Epoch)',
    'End Date/time',
    'End Date/time (Epoch)',
    'Created By Caregiver',
    'Last Updated By Caregiver',
    'Note',
    'Time Zone',
    '_familyKey',
    '_profileKey',
    '_activityKey',
)

DIAPER_STATUS = {
    'wet': Event.DiaperType.WET,
    'pee': Event.DiaperType.WET,
    'dirty': Event.DiaperType.DIRTY,
    'poop': Event.DiaperType.DIRTY,
    'mixed': Event.DiaperType.BOTH,
    'both': Event.DiaperType.BOTH,
    'wet and dirty': Event.DiaperType.BOTH,
    'dirty wet': Event.DiaperType.BOTH,
    'dry': Event.DiaperType.DRY,
}

BOTTLE_TYPES = {
    'breast milk': 'breast_milk',
    'formula': 'formula',
    'breast milk formula': 'mixed',
}

ML_PER_FLOZ = 29.5735295625

RECORD_KIND = {
    'breastfeed': Event.Kind.FEED,
    'bottle feed': Event.Kind.BOTTLE,
    'pump': Event.Kind.PUMP,
    'diaper': Event.Kind.DIAPER,
    'sleep': Event.Kind.SLEEP,
    'growth': Event.Kind.GROWTH,
    'medical': Event.Kind.MEDICAL,
    'medicine': Event.Kind.MEDICAL,
    'temperature': Event.Kind.MEDICAL,
    'profile': 'profile',
}


class RollbackNeedsDecision(ValidationError):
    def __init__(self, events):
        self.events = list(events)
        super().__init__('Some imported records were edited later.')


@dataclass
class ParsedRow:
    row_number: int
    record_type: str = ''
    activity_id: str = ''
    classification: str = ImportRow.Classification.OK
    warnings: list = field(default_factory=list)
    mapped: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def warn(self, code: str, **detail) -> None:
        self.warnings.append({'code': code, **detail})
        if self.classification != ImportRow.Classification.ERROR:
            self.classification = ImportRow.Classification.WARNING

    def fail(self, code: str) -> None:
        self.warnings.append({'code': code})
        self.classification = ImportRow.Classification.ERROR


def parse_csv(stream) -> list[ParsedRow]:
    """Parse a text stream. Malformed rows keep their number, not their text."""
    reader = csv.DictReader(stream)
    rows: list[ParsedRow] = []
    number = 1
    try:
        for raw in reader:
            number += 1
            rows.append(_parse_raw(number, raw or {}))
    except csv.Error:
        logger.warning('import row %s malformed (%s)', number, 'csv.Error')
        broken = ParsedRow(row_number=number)
        broken.fail('malformed')
        rows.append(broken)
    return rows


def _parse_raw(number: int, raw: dict) -> ParsedRow:
    cleaned = {
        (key or '').strip(): ('' if value is None else str(value).strip())
        for key, value in raw.items()
        if key is not None
    }
    row = ParsedRow(row_number=number, raw=cleaned)
    record_type = cleaned.get('Type', '')
    row.record_type = record_type
    row.activity_id = cleaned.get('_activityKey', '')
    if not record_type:
        row.fail('missing_type')
        return row
    kind = RECORD_KIND.get(record_type.strip().lower())
    if kind is None:
        row.mapped = {
            'kind': Event.Kind.UNKNOWN,
            'original_type': record_type,
            'note': cleaned.get('Note', ''),
            'caregiver_name': cleaned.get('Created By Caregiver', ''),
            'photo_names': _photos(cleaned),
        }
        row.warn('unknown_type')
        _attach_times(row, cleaned)
        row.mapped['fingerprint'] = fingerprint(row.mapped)
        return row
    if kind == 'profile':
        row.mapped = {
            'kind': 'profile',
            'profile_name': cleaned.get('Profile Name', ''),
            'birth_date': cleaned.get('[Profile] Birth Date', ''),
            'birth_date_adjusted': cleaned.get('[Profile] Birth Date (Adjusted)', ''),
            'sex': cleaned.get('[Profile] Sex', ''),
            'profile_type': cleaned.get('[Profile] Type', ''),
        }
        return row
    if not row.activity_id:
        row.fail('missing_activity_id')
        return row
    row.mapped = {
        'kind': kind,
        'note': cleaned.get('Note', ''),
        'caregiver_name': cleaned.get('Created By Caregiver', ''),
        'editor_name': cleaned.get('Last Updated By Caregiver', ''),
        'photo_names': _photos(cleaned),
        'profile_name': cleaned.get('Profile Name', ''),
    }
    _attach_times(row, cleaned)
    if kind == Event.Kind.FEED:
        _breastfeed(row, cleaned)
    elif kind == Event.Kind.BOTTLE:
        _bottle(row, cleaned)
    elif kind == Event.Kind.PUMP:
        _pump(row, cleaned)
    elif kind == Event.Kind.DIAPER:
        _diaper(row, cleaned)
    elif kind == Event.Kind.SLEEP:
        _sleep(row, cleaned)
    elif kind == Event.Kind.GROWTH:
        _growth(row, cleaned)
    elif kind == Event.Kind.MEDICAL:
        _medical(row, cleaned)
    row.mapped['fingerprint'] = fingerprint(row.mapped)
    return row


def _photos(cleaned: dict) -> list[str]:
    name = cleaned.get('[Photo] Filename', '')
    if not name:
        return []
    return [name]


def _attach_times(row: ParsedRow, cleaned: dict) -> None:
    zone_name = cleaned.get('Time Zone') or 'UTC'
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        row.warn('unknown_timezone', zone=zone_name)
        zone = ZoneInfo('UTC')
        zone_name = 'UTC'
    row.mapped['timezone'] = zone_name
    readable = _wall_time(
        row,
        cleaned.get('Start Date/time', ''),
        zone,
        'start',
    )
    epoch = _epoch(cleaned.get('Start Date/time (Epoch)', ''))
    _compare_clocks(row, readable, epoch, 'start')
    chosen = _choose_instant(readable, epoch)
    if chosen is None and row.mapped.get('kind') != 'profile':
        row.fail('missing_start')
        return
    if chosen is not None:
        row.mapped['occurred_at'] = chosen.astimezone(timezone.utc).isoformat()
    end_readable = _wall_time(
        row,
        cleaned.get('End Date/time', ''),
        zone,
        'end',
    )
    end_epoch = _epoch(cleaned.get('End Date/time (Epoch)', ''))
    if end_readable or end_epoch:
        _compare_clocks(row, end_readable, end_epoch, 'end')
        end_chosen = _choose_instant(end_readable, end_epoch)
        if end_chosen is not None:
            row.mapped['ended_at'] = end_chosen.astimezone(timezone.utc).isoformat()


def _wall_time(row: ParsedRow, text: str, zone: ZoneInfo, label: str):
    if not text:
        return None
    parsed = None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S'):
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        row.warn('unparsed_time', label=label)
        return None
    first = parsed.replace(tzinfo=zone, fold=0)
    second = parsed.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        row.warn(
            'ambiguous_time',
            label=label,
            first=first.astimezone(timezone.utc).isoformat(),
            second=second.astimezone(timezone.utc).isoformat(),
        )
    back = first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
    if back != parsed:
        row.warn(
            'nonexistent_time',
            label=label,
            wall=text,
            used=first.astimezone(timezone.utc).isoformat(),
        )
    return first


def _epoch(text: str):
    if text == '':
        return None
    try:
        millis = int(text)
    except ValueError:
        return 'invalid'
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def _compare_clocks(row, readable, epoch, label: str) -> None:
    if epoch == 'invalid':
        row.warn('invalid_epoch', label=label)
        return
    if readable is None or epoch is None:
        return
    delta = abs((readable - epoch).total_seconds())
    if delta > EPOCH_TOLERANCE_SECONDS:
        row.warn(
            'epoch_mismatch',
            label=label,
            readable=readable.astimezone(timezone.utc).isoformat(),
            epoch=epoch.isoformat(),
        )


def _choose_instant(readable, epoch):
    if epoch == 'invalid':
        epoch = None
    if readable is not None and epoch is not None:
        delta = abs((readable - epoch).total_seconds())
        if delta > EPOCH_TOLERANCE_SECONDS:
            return readable
        return epoch
    return readable or epoch


def _duration(row: ParsedRow, raw: str, unit: str, label: str):
    if raw == '':
        return None
    try:
        number = float(raw)
    except ValueError:
        row.warn('bad_duration', label=label)
        return None
    if number < 0:
        row.warn('negative_duration', label=label)
        return None
    normalized = unit.strip().lower()
    if normalized in {'s', 'sec', 'secs', 'second', 'seconds'}:
        seconds = int(round(number))
    elif normalized in {'m', 'min', 'mins', 'minute', 'minutes'}:
        seconds = int(round(number * 60))
    elif normalized in {'ms', 'millisecond', 'milliseconds'}:
        seconds = int(round(number / 1000))
    else:
        row.warn('missing_or_unknown_unit', label=label, unit=unit or '')
        return None
    return seconds


def _strip_nontimer(value: str) -> tuple[str, bool]:
    text = value.strip()
    if text.lower().endswith('.nontimer'):
        return text[: -len('.nontimer')].strip(), True
    return text, False


def _breast_duration(row: ParsedRow, cleaned: dict, side: str):
    """Integer seconds column, or the older duration-plus-unit columns."""
    seconds = cleaned.get(f'[Breastfeed] {side} Duration (Seconds)', '')
    if seconds != '':
        return _duration(row, seconds, 's', side.lower())
    unit = cleaned.get('[Breastfeed] Duration Unit', '')
    return _duration(
        row,
        cleaned.get(f'[Breastfeed] {side} Duration', ''),
        unit,
        side.lower(),
    )


def _breastfeed(row: ParsedRow, cleaned: dict) -> None:
    row.mapped['left_seconds'] = _breast_duration(row, cleaned, 'Left')
    row.mapped['right_seconds'] = _breast_duration(row, cleaned, 'Right')
    start_raw = cleaned.get('[Breastfeed] Begin Side', '') or cleaned.get(
        '[Breastfeed] Start Side', ''
    )
    end_raw = cleaned.get('[Breastfeed] End Side', '')
    start_text, start_manual = _strip_nontimer(start_raw)
    end_text, end_manual = _strip_nontimer(end_raw)
    start = _side(start_text)
    end = _side(end_text)
    if start_raw and start is None:
        row.warn('unknown_side', label='start')
    if end_raw and end is None:
        row.warn('unknown_side', label='end')
    row.mapped['start_side'] = start or ''
    row.mapped['end_side'] = end or ''
    if start_manual or end_manual:
        row.mapped['manual_entry'] = True


def _side(value: str) -> str | None:
    lowered = value.strip().lower()
    if lowered in {'l', 'left'}:
        return Event.Side.LEFT
    if lowered in {'r', 'right'}:
        return Event.Side.RIGHT
    if not lowered:
        return ''
    return None


def _measurement(row: ParsedRow, amount: str, unit: str, label: str):
    """A volume. An empty amount is unknown and does not warn, even if the unit is empty."""
    if amount == '':
        return None
    try:
        number = float(amount)
    except ValueError:
        row.warn('bad_measurement', field=label)
        return {'amount': amount, 'unit': unit, 'ml': None}
    normalized = unit.strip().lower()
    if normalized == 'ml':
        ml = number
    elif normalized == 'floz':
        ml = number * ML_PER_FLOZ
    elif normalized == '':
        row.warn('missing_or_unknown_unit', label=label, unit='')
        ml = None
    else:
        row.warn('missing_or_unknown_unit', label=label, unit=unit)
        ml = None
    return {'amount': number, 'unit': unit.strip(), 'ml': ml}


def _known_ml(items) -> float | None:
    values = [
        item['ml'] for item in items if item is not None and item.get('ml') is not None
    ]
    if not values:
        return None
    return float(sum(values))


def _bottle(row: ParsedRow, cleaned: dict) -> None:
    raw_type = cleaned.get('[Bottle Feed] Type', '')
    bottle_type = ''
    if raw_type:
        bottle_type = BOTTLE_TYPES.get(raw_type.strip().lower(), '')
        if not bottle_type:
            row.warn('unknown_enum', field='bottle', value=raw_type)
    volume = _measurement(
        row,
        cleaned.get('[Bottle Feed] Volume', ''),
        cleaned.get('[Bottle Feed] Volume Unit', ''),
        'bottle_volume',
    )
    breast = _measurement(
        row,
        cleaned.get('[Bottle Feed] Breast Milk Volume', ''),
        cleaned.get('[Bottle Feed] Breast Milk Volume Unit', ''),
        'breast_milk_volume',
    )
    formula = _measurement(
        row,
        cleaned.get('[Bottle Feed] Formula Volume', ''),
        cleaned.get('[Bottle Feed] Formula Volume Unit', ''),
        'formula_volume',
    )
    volume_ml = volume.get('ml') if volume and volume.get('ml') is not None else None
    if volume_ml is None:
        volume_ml = _known_ml((breast, formula))
    row.mapped['archive'] = {
        'bottle_type': bottle_type,
        'bottle_type_raw': raw_type,
        'formula_name': cleaned.get('[Bottle Feed] Formula Name', ''),
        'volume': volume,
        'breast_milk': breast,
        'formula': formula,
        'volume_ml': volume_ml,
    }


def _pump(row: ParsedRow, cleaned: dict) -> None:
    if not row.mapped.get('ended_at'):
        epoch = _epoch(cleaned.get('[Pump] End Date/time (Epoch)', ''))
        if epoch not in (None, 'invalid'):
            row.mapped['ended_at'] = epoch.astimezone(timezone.utc).isoformat()
        elif epoch == 'invalid':
            row.warn('invalid_epoch', label='pump_end')
    left = _measurement(
        row,
        cleaned.get('[Pump] Left Volume', ''),
        cleaned.get('[Pump] Left Volume Unit', ''),
        'pump_left',
    )
    right = _measurement(
        row,
        cleaned.get('[Pump] Right Volume', ''),
        cleaned.get('[Pump] Right Volume Unit', ''),
        'pump_right',
    )
    total = _measurement(
        row,
        cleaned.get('[Pump] Total Volume', ''),
        cleaned.get('[Pump] Total Volume Unit', ''),
        'pump_total',
    )
    volume_ml = total.get('ml') if total and total.get('ml') is not None else None
    if volume_ml is None:
        volume_ml = _known_ml((left, right))
    duration = cleaned.get('[Pump] Duration (Seconds)', '')
    row.mapped['archive'] = {
        'left': left,
        'right': right,
        'total': total,
        'duration_seconds': duration,
        'volume_ml': volume_ml,
    }


def _diaper(row: ParsedRow, cleaned: dict) -> None:
    raw = cleaned.get('[Diaper] Type', '') or cleaned.get('[Diaper] Status', '')
    detail = {
        'detail': cleaned.get('[Diaper] Detail', ''),
        'dirty_color': cleaned.get('[Diaper] Dirty Color', ''),
        'dirty_texture': cleaned.get('[Diaper] Dirty Texture', ''),
    }
    if any(detail.values()):
        row.mapped['archive'] = detail
    if raw == '':
        row.warn('missing_diaper_status')
        row.mapped['diaper_type'] = ''
        return
    mapped = DIAPER_STATUS.get(raw.strip().lower())
    if mapped is None:
        row.warn('unknown_enum', field='diaper', value=raw)
        row.mapped['diaper_type'] = ''
        row.mapped['raw_status'] = raw
        return
    row.mapped['diaper_type'] = mapped


def _sleep(row: ParsedRow, cleaned: dict) -> None:
    if not row.mapped.get('ended_at'):
        zone = ZoneInfo(row.mapped.get('timezone') or 'UTC')
        readable = _wall_time(
            row,
            cleaned.get('[Sleep] End Date/time', ''),
            zone,
            'sleep_end',
        )
        epoch = _epoch(cleaned.get('[Sleep] End Date/time (Epoch)', ''))
        if readable or epoch:
            _compare_clocks(row, readable, epoch, 'sleep_end')
            chosen = _choose_instant(readable, epoch)
            if chosen is not None:
                row.mapped['ended_at'] = chosen.astimezone(timezone.utc).isoformat()
    row.mapped['archive'] = {
        'ended_at': row.mapped.get('ended_at'),
        'duration_seconds': cleaned.get('[Sleep] Duration (Seconds)', ''),
    }


def _growth(row: ParsedRow, cleaned: dict) -> None:
    weight = cleaned.get('[Growth] Weight', '')
    weight_unit = cleaned.get('[Growth] Weight Unit', '')
    height = cleaned.get('[Growth] Height', '')
    height_unit = cleaned.get('[Growth] Height Unit', '')
    head = cleaned.get('[Growth] Head Size', '') or cleaned.get('[Growth] Head', '')
    head_unit = cleaned.get('[Growth] Head Size Unit', '') or cleaned.get(
        '[Growth] Head Unit', ''
    )
    _flag_unit_pair(row, weight, weight_unit, 'weight')
    _flag_unit_pair(row, height, height_unit, 'height')
    _flag_unit_pair(row, head, head_unit, 'head')
    row.mapped['archive'] = {
        'weight': weight,
        'weight_unit': weight_unit,
        'height': height,
        'height_unit': height_unit,
        'head': head,
        'head_unit': head_unit,
    }


def _medical(row: ParsedRow, cleaned: dict) -> None:
    value = cleaned.get('[Temperature] Value', '') or cleaned.get('[Medical] Dose', '')
    unit = cleaned.get('[Temperature] Unit', '') or cleaned.get(
        '[Medical] Dose Unit', ''
    )
    if value != '' and unit != '':
        _temperature(row, value, unit)
    name = cleaned.get('[Medical] Medication', '') or cleaned.get('[Medical] Name', '')
    row.mapped['archive'] = {
        'name': name,
        'dose': cleaned.get('[Medical] Dose', ''),
        'dose_unit': cleaned.get('[Medical] Dose Unit', ''),
        'temperature': cleaned.get('[Temperature] Value', ''),
        'temperature_unit': cleaned.get('[Temperature] Unit', ''),
    }


def _temperature(row: ParsedRow, value: str, unit: str) -> None:
    try:
        number = float(value)
    except ValueError:
        row.warn('bad_measurement', field='temperature')
        return
    normalized = unit.strip().lower()
    implausible = False
    if normalized in {'c', 'celsius'} and (number < 30 or number > 45):
        implausible = True
    if normalized in {'f', 'fahrenheit'} and (number < 90 or number > 110):
        implausible = True
    if implausible:
        row.warn('implausible_unit', field='temperature', unit=unit)
    if normalized not in {'c', 'celsius', 'f', 'fahrenheit'}:
        row.warn('unknown_unit', field='temperature', unit=unit)


def _flag_unit_pair(row, value: str, unit: str, field_name: str) -> None:
    if value == '':
        return
    if unit == '':
        row.warn('missing_or_unknown_unit', label=field_name, unit='')


def fingerprint(mapped: dict) -> str:
    payload = {
        'kind': mapped.get('kind'),
        'occurred_at': mapped.get('occurred_at'),
        'ended_at': mapped.get('ended_at'),
        'left_seconds': mapped.get('left_seconds'),
        'right_seconds': mapped.get('right_seconds'),
        'start_side': mapped.get('start_side'),
        'end_side': mapped.get('end_side'),
        'diaper_type': mapped.get('diaper_type'),
        'note': mapped.get('note') or '',
        'archive': mapped.get('archive') or {},
        'photo_names': mapped.get('photo_names') or [],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _read_upload(uploaded) -> bytes:
    return b''.join(uploaded.chunks())


def stored_bytes(batch) -> bytes | None:
    """CSV bytes from PostgreSQL, or None after retention purge."""
    from tracker.extra_models import ImportFile

    if not batch.stored_name:
        return None
    row = ImportFile.objects.filter(batch=batch).only('content').first()
    if row is None:
        return None
    content = row.content
    if isinstance(content, memoryview):
        return content.tobytes()
    return bytes(content)


def stage_upload(*, actor, household, baby, uploaded, text: str, now=None):
    from tracker.extra_models import ImportFile

    now = now or dj_timezone.now()
    content = _read_upload(uploaded)
    batch = ImportBatch(
        household=household,
        baby=baby,
        status=ImportBatch.Status.STAGED,
        original_name=(uploaded.name or '')[:255],
        stored_name='postgres',
        purge_after=now + timedelta(days=settings.IMPORT_RETENTION_DAYS),
    )
    batch.save(actor=actor)
    ImportFile.objects.create(batch=batch, content=content)
    _fill_rows(batch, text)
    return batch


def stage_text(*, actor, household, baby, text: str, now=None, filename: str = ''):
    """Stage CSV text that is already in memory (tests)."""
    now = now or dj_timezone.now()
    batch = ImportBatch(
        household=household,
        baby=baby,
        status=ImportBatch.Status.STAGED,
        original_name=filename[:255],
        purge_after=now + timedelta(days=settings.IMPORT_RETENTION_DAYS),
    )
    batch.save(actor=actor)
    _fill_rows(batch, text)
    return batch


def _fill_rows(batch, text: str) -> None:
    parsed = parse_csv(io.StringIO(text))
    seen: dict[str, int] = {}
    profile = None
    counts: dict[str, int] = {}
    for row in parsed:
        if row.activity_id:
            if row.activity_id in seen:
                row.warn('duplicate_in_file')
                row.mapped['duplicate_of_row'] = seen[row.activity_id]
            else:
                seen[row.activity_id] = row.row_number
        if row.mapped.get('kind') == 'profile':
            profile = {
                'profile_name': row.mapped.get('profile_name', ''),
                'birth_date': row.mapped.get('birth_date', ''),
                'birth_date_adjusted': row.mapped.get('birth_date_adjusted', ''),
                'sex': row.mapped.get('sex', ''),
                'profile_type': row.mapped.get('profile_type', ''),
            }
        disposition = _disposition(batch, row)
        ImportRow.objects.create(
            batch=batch,
            row_number=row.row_number,
            record_type=row.record_type,
            source_activity_id=row.activity_id,
            classification=row.classification,
            disposition=disposition,
            decision='pending',
            warnings=row.warnings,
            mapped=row.mapped,
            raw=row.raw,
            fingerprint=row.mapped.get('fingerprint', ''),
        )
        counts[row.record_type or 'blank'] = (
            counts.get(row.record_type or 'blank', 0) + 1
        )
    batch.profile = profile
    batch.summary = {'counts': counts, 'rows': len(parsed)}
    batch.save(actor=batch.created_by, expected_version=batch.version)
    logger.info(
        'import staged batch=%s rows=%s',
        batch.pk,
        len(parsed),
    )


def _disposition(batch, row: ParsedRow) -> str:
    if row.mapped.get('kind') == 'profile':
        return 'profile'
    if row.classification == ImportRow.Classification.ERROR:
        return 'blocked'
    if any(item.get('code') == 'duplicate_in_file' for item in row.warnings):
        return 'duplicate'
    if not row.activity_id:
        return 'new'
    existing = Event.all_objects.filter(
        household=batch.household,
        source_system=SOURCE,
        source_activity_id=row.activity_id,
    ).first()
    if existing is None:
        return 'new'
    if existing.deleted_at is not None:
        return 'reimport'
    if existing.import_fingerprint and existing.import_fingerprint == row.mapped.get(
        'fingerprint'
    ):
        return 'already'
    return 'changed'


def set_name_map(batch, name_map: dict) -> None:
    """Map an export name to a user id, or leave it unmapped.

    Unmapped names stay attribution text. This never creates a user.
    """
    cleaned = {}
    for name, user_id in name_map.items():
        cleaned[str(name)] = str(user_id) if user_id else ''
    batch.name_map = cleaned
    batch.save(actor=batch.updated_by, expected_version=batch.version)


def set_decisions(batch, decisions: dict[str, str]) -> None:
    for row in batch.rows.all():
        choice = decisions.get(str(row.pk)) or decisions.get(row.source_activity_id)
        if choice in {'keep', 'apply', 'pending'}:
            row.decision = choice
            row.save(update_fields=['decision'])


def abandon(batch, *, actor) -> None:
    if batch.status != ImportBatch.Status.STAGED:
        raise ValidationError('Only a staged batch can be abandoned.')
    batch.status = ImportBatch.Status.ABANDONED
    batch.save(actor=actor, expected_version=batch.version)


@transaction.atomic
def commit_batch(batch, *, actor, skip_errors: bool = False):
    locked = ImportBatch.all_objects.select_for_update().get(pk=batch.pk)
    if locked.status != ImportBatch.Status.STAGED:
        raise ValidationError('This batch is not staged.')
    errors = locked.rows.filter(classification=ImportRow.Classification.ERROR)
    if errors.exists() and not skip_errors:
        raise ValidationError('Fix or skip rows with errors before committing.')
    created = 0
    for row in locked.rows.select_for_update().order_by('row_number'):
        if row.classification == ImportRow.Classification.ERROR:
            continue
        if row.disposition in {'profile', 'duplicate', 'blocked', 'already'}:
            continue
        if row.disposition == 'changed' and row.decision != 'apply':
            continue
        event = _write_row(locked, row, actor)
        if event is not None:
            row.event = event
            row.save(update_fields=['event'])
            created += 1
    locked.status = ImportBatch.Status.COMMITTED
    summary = dict(locked.summary or {})
    summary['committed'] = created
    locked.summary = summary
    locked.save(actor=actor, expected_version=locked.version)
    logger.info('import committed batch=%s records=%s', locked.pk, created)
    return locked


def _write_row(batch, row, actor):
    mapped = row.mapped or {}
    kind = mapped.get('kind')
    if kind in (None, 'profile'):
        return None
    occurred = _parse_iso(mapped.get('occurred_at'))
    if occurred is None:
        return None
    ended = _parse_iso(mapped.get('ended_at'))
    name = mapped.get('caregiver_name') or ''
    user_id = (batch.name_map or {}).get(name) or ''
    attributed_to_id = user_id or None
    fields = {
        'kind': kind if kind in Event.Kind.values else Event.Kind.UNKNOWN,
        'occurred_at': occurred,
        'notes': (mapped.get('note') or '')[:2000],
        'attributed_name': name[:200],
        'attributed_to_id': attributed_to_id,
        'photo_names': mapped.get('photo_names') or [],
        'archive': mapped.get('archive'),
        'import_fingerprint': mapped.get('fingerprint') or row.fingerprint,
        'source_system': SOURCE,
        'source_activity_id': row.source_activity_id,
        'import_batch': batch,
    }
    if kind == Event.Kind.DIAPER:
        fields['diaper_type'] = mapped.get('diaper_type') or ''
        if not fields['diaper_type']:
            fields['kind'] = Event.Kind.UNKNOWN
            fields['archive'] = {
                'raw_status': mapped.get('raw_status', ''),
                **(mapped.get('archive') or {}),
            }
    if kind == Event.Kind.FEED:
        fields.update(
            {
                'feed_state': Event.FeedState.FINISHED,
                'feed_source': (
                    Event.FeedSource.MANUAL
                    if mapped.get('manual_entry')
                    else Event.FeedSource.IMPORT
                ),
                'feed_ended_at': ended,
                'left_seconds': mapped.get('left_seconds'),
                'right_seconds': mapped.get('right_seconds'),
                'start_side': mapped.get('start_side') or '',
                'end_side': mapped.get('end_side') or '',
                'totals_overridden': True,
            }
        )
    existing = Event.all_objects.filter(
        household=batch.household,
        source_system=SOURCE,
        source_activity_id=row.source_activity_id,
    ).first()
    if existing is None:
        event = Event(baby=batch.baby, recorded_at=dj_timezone.now(), **fields)
        event.save(actor=actor)
        ImportLink.objects.create(
            batch=batch,
            event=event,
            action=ImportLink.Action.CREATED,
            preimage={},
            imported_version=event.version,
        )
        return event
    preimage = snapshot(existing)
    was_deleted = existing.deleted_at is not None
    if was_deleted:
        existing.restore(actor=actor, expected_version=existing.version)
    for name_key, value in fields.items():
        setattr(existing, name_key, value)
    existing.baby = batch.baby
    existing.save(actor=actor, expected_version=existing.version)
    ImportLink.objects.create(
        batch=batch,
        event=existing,
        action=(
            ImportLink.Action.RESTORED if was_deleted else ImportLink.Action.UPDATED
        ),
        preimage=preimage,
        imported_version=existing.version,
    )
    return existing


def _parse_iso(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return None
    return parsed


MUTABLE_REVERT = (
    'occurred_at',
    'notes',
    'diaper_type',
    'feed_state',
    'feed_source',
    'feed_ended_at',
    'left_seconds',
    'right_seconds',
    'start_side',
    'end_side',
    'totals_overridden',
    'attributed_name',
    'attributed_to_id',
    'import_fingerprint',
    'photo_names',
    'archive',
    'kind',
    'source_system',
    'source_activity_id',
)


def rollback_preview(batch):
    edited = []
    for link in batch.links.select_related('event'):
        event = Event.all_objects.get(pk=link.event_id)
        if event.version != link.imported_version:
            edited.append(event)
    return edited


@transaction.atomic
def rollback_batch(batch, *, actor, decisions: dict | None = None):
    decisions = decisions or {}
    locked = ImportBatch.all_objects.select_for_update().get(pk=batch.pk)
    if locked.status != ImportBatch.Status.COMMITTED:
        raise ValidationError('Only a committed batch can be rolled back.')
    pending = []
    links = list(locked.links.select_related('event'))
    for link in links:
        event = Event.all_objects.select_for_update().get(pk=link.event_id)
        if event.version != link.imported_version:
            choice = decisions.get(str(event.pk))
            if choice not in {'keep', 'remove'}:
                pending.append(event)
    if pending:
        raise RollbackNeedsDecision(pending)
    for link in links:
        event = Event.all_objects.select_for_update().get(pk=link.event_id)
        choice = decisions.get(str(event.pk))
        if event.version != link.imported_version and choice == 'keep':
            link.rollback_choice = 'keep'
            link.save(update_fields=['rollback_choice'])
            continue
        if link.action == ImportLink.Action.UPDATED and choice != 'remove':
            _revert_update(event, link, actor)
        else:
            if event.deleted_at is None:
                event.delete(actor=actor, expected_version=event.version)
        link.rollback_choice = choice or 'remove'
        link.save(update_fields=['rollback_choice'])
    locked.status = ImportBatch.Status.ROLLED_BACK
    locked.save(actor=actor, expected_version=locked.version)
    logger.info('import rolled back batch=%s', locked.pk)
    return locked


def _revert_update(event, link, actor):
    preimage = link.preimage or {}
    for name in MUTABLE_REVERT:
        source_name = 'attributed_to' if name == 'attributed_to_id' else name
        if source_name not in preimage:
            continue
        value = preimage[source_name]
        if name in {'occurred_at', 'feed_ended_at'} and isinstance(value, str):
            value = _parse_iso(value)
        setattr(event, name, value)
    event.save(actor=actor, expected_version=event.version)


def backfill_import_fields(*, dry_run: bool = False, batch_id=None) -> dict:
    """Fill import fields that were stored empty because a column was unread.

    Only NULL or blank fields are written, and only on events that still
    match the imported version. Counts are the only values returned.
    """
    from tracker.extra_models import ImportBatch, ImportRow

    counts = {
        'examined': 0,
        'updated': 0,
        'skipped_edited': 0,
        'unchanged': 0,
    }
    rows = ImportRow.objects.filter(
        batch__status=ImportBatch.Status.COMMITTED,
        event__isnull=False,
    ).select_related('event', 'batch__created_by')
    if batch_id:
        rows = rows.filter(batch_id=batch_id)
    for row in rows.order_by('row_number'):
        counts['examined'] += 1
        event = row.event
        if event.deleted_at is not None:
            counts['unchanged'] += 1
            continue
        link = event.import_links.order_by('-imported_version').first()
        imported_version = link.imported_version if link is not None else 1
        if event.version != imported_version:
            counts['skipped_edited'] += 1
            continue
        parsed = _parse_raw(row.row_number, row.raw or {})
        if not _fill_missing(event, parsed.mapped):
            counts['unchanged'] += 1
            continue
        counts['updated'] += 1
        if dry_run:
            continue
        event.save(actor=row.batch.created_by, expected_version=event.version)
        if link is not None:
            link.imported_version = event.version
            link.save(update_fields=['imported_version'])
    return counts


def _fill_missing(event, mapped: dict) -> bool:
    kind = mapped.get('kind')
    if kind == Event.Kind.FEED:
        if event.feed_source != Event.FeedSource.IMPORT:
            return False
        changed = False
        changed |= _set_if_null(event, 'left_seconds', mapped.get('left_seconds'))
        changed |= _set_if_null(event, 'right_seconds', mapped.get('right_seconds'))
        changed |= _set_if_blank(event, 'start_side', mapped.get('start_side') or '')
        changed |= _set_if_blank(event, 'end_side', mapped.get('end_side') or '')
        if event.feed_ended_at is None:
            ended = _parse_iso(mapped.get('ended_at'))
            if ended is not None:
                event.feed_ended_at = ended
                changed = True
        return changed
    if kind in (Event.Kind.BOTTLE, Event.Kind.PUMP):
        return _fill_archive(event, mapped.get('archive') or {}, volume=True)
    if kind == Event.Kind.SLEEP:
        return _fill_archive(
            event,
            mapped.get('archive') or {},
            keys=('duration_seconds', 'ended_at'),
        )
    if kind == Event.Kind.DIAPER:
        changed = _fill_archive(
            event,
            mapped.get('archive') or {},
            keys=('detail', 'dirty_color', 'dirty_texture'),
        )
        if not event.diaper_type and mapped.get('diaper_type'):
            event.diaper_type = mapped['diaper_type']
            changed = True
        return changed
    return False


def _set_if_null(event, field: str, value) -> bool:
    if value is None or getattr(event, field) is not None:
        return False
    setattr(event, field, value)
    return True


def _set_if_blank(event, field: str, value: str) -> bool:
    if not value or getattr(event, field):
        return False
    setattr(event, field, value)
    return True


def _fill_archive(event, fresh: dict, *, keys=(), volume: bool = False) -> bool:
    if not fresh:
        return False
    current = dict(event.archive or {})
    changed = False
    if (
        volume
        and current.get('volume_ml') in (None, '')
        and fresh.get('volume_ml')
        not in (
            None,
            '',
        )
    ):
        current['volume_ml'] = fresh['volume_ml']
        changed = True
    names = keys or (
        'volume',
        'total',
        'left',
        'right',
        'breast_milk',
        'formula',
        'bottle_type',
        'duration_seconds',
        'ended_at',
    )
    for name in names:
        if current.get(name) not in (None, '') or fresh.get(name) in (None, ''):
            continue
        current[name] = fresh[name]
        changed = True
    if changed:
        event.archive = current
    return changed


def purge_expired(*, now=None) -> int:
    from tracker.extra_models import ImportFile

    now = now or dj_timezone.now()
    removed = 0
    batches = ImportBatch.all_objects.filter(purge_after__lt=now).exclude(
        stored_name=''
    )
    for batch in batches:
        ImportFile.objects.filter(batch=batch).delete()
        batch.stored_name = ''
        batch.purged_at = now
        batch.save(actor=batch.updated_by, expected_version=batch.version)
        removed += 1
    return removed
