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
