from django import template

register = template.Library()


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
