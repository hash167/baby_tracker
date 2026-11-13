from django import template

from tracker.diapers import DIAPER_LABELS, DIAPER_MARKS

register = template.Library()


@register.filter
def diaper_label(value):
    if value is None or value == '':
        return ''
    return DIAPER_LABELS.get(str(value), str(value))


@register.filter
def diaper_mark(value):
    return DIAPER_MARKS.get(str(value or ''), '')


@register.simple_tag
def diaper_choices():
    return [
        {'value': key, 'label': label, 'mark': DIAPER_MARKS[key]}
        for key, label in DIAPER_LABELS.items()
    ]


@register.filter
def duration(seconds):
    if seconds is None:
        return 'unknown'
    total = int(seconds)
    sign = '-' if total < 0 else ''
    total = abs(total)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{sign}{hours}:{minutes:02d}:{secs:02d}'
    return f'{sign}{minutes}:{secs:02d}'


@register.filter
def minutes(seconds):
    if seconds is None:
        return ''
    text = f'{int(seconds) / 60:.2f}'
    return text.rstrip('0').rstrip('.')


BOTTLE_TYPE_LABELS = {
    'breast_milk': 'Breast milk',
    'formula': 'Formula',
    'mixed': 'Breast milk + formula',
}


def _format_amount(amount, unit: str) -> str:
    if isinstance(amount, float) and amount.is_integer():
        amount = int(amount)
    normalized = (unit or '').strip().lower()
    if normalized == 'ml':
        return f'{amount} ml'
    if normalized == 'floz':
        return f'{amount} fl oz'
    if not normalized:
        return str(amount)
    return f'{amount} {unit}'


def _format_measurement(item) -> str:
    if not item or item.get('amount') in (None, ''):
        return ''
    return _format_amount(item.get('amount'), item.get('unit') or '')


def _format_ml(value) -> str:
    if value is None:
        return 'unknown'
    number = float(value)
    if number.is_integer():
        number = int(number)
    else:
        number = round(number, 1)
    return f'{number} ml'


@register.filter
def ml(value):
    return _format_ml(value)


@register.filter
def activity_label(event):
    archive = event.archive or {}
    if event.kind == 'bottle':
        parts = ['Bottle']
        type_label = BOTTLE_TYPE_LABELS.get(archive.get('bottle_type') or '')
        if type_label:
            parts.append(type_label)
        shown = _format_measurement(archive.get('volume'))
        if not shown and archive.get('volume_ml') is not None:
            shown = _format_ml(archive.get('volume_ml'))
        if shown:
            parts.append(shown)
        return ' · '.join(parts)
    if event.kind == 'pump':
        shown = _format_measurement(archive.get('total'))
        if not shown and archive.get('volume_ml') is not None:
            shown = _format_ml(archive.get('volume_ml'))
        if shown:
            return f'Pump · {shown}'
        return 'Pump'
    return event.get_kind_display()


@register.filter
def one_decimal(value):
    if value is None:
        return '0'
    return f'{float(value):.1f}'


@register.filter
def signed(value):
    number = float(value or 0)
    rounded = f'{abs(number):.1f}'
    if number > 0:
        return f'+{rounded}'
    if number < 0:
        return f'−{rounded}'
    return '0'


@register.filter
def gap(seconds):
    if seconds is None:
        return '—'
    total = int(round(float(seconds)))
    if total < 0:
        total = 0
    minutes, _secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours}h {minutes}m'
    return f'{minutes}m'


@register.filter
def ago(delta):
    if delta is None:
        return '—'
    total = int(delta.total_seconds())
    if total < 0:
        total = 0
    minutes, _secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours}h {minutes}m'
    return f'{minutes}m'
