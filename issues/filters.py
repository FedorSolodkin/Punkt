"""Общие фильтры реестра и отчёта (ТЗ §4, §6.2): status, due_from, due_to, overdue."""
from datetime import date

from django.db.models import Q
from django.utils import timezone

from core.errors import ValidationError

from .models import IssueStatus


def parse_iso_date(value, field_name):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError('Неверный формат даты', fields={field_name: 'Формат YYYY-MM-DD'}) from exc


def apply_filters(qs, get_params):
    """Возвращает (queryset, человекочитаемое_описание_фильтров)."""
    parts = []

    status = get_params.get('status')
    if status:
        if status not in IssueStatus.values:
            raise ValidationError('Неизвестный статус', fields={'status': 'OPEN, READY или CLOSED'})
        qs = qs.filter(status=status)
        parts.append(f'статус={status}')

    due_from = due_to = None
    if get_params.get('due_from'):
        due_from = parse_iso_date(get_params['due_from'], 'due_from')
        qs = qs.filter(due_date__gte=due_from)
        parts.append(f'срок от {due_from.isoformat()}')
    if get_params.get('due_to'):
        due_to = parse_iso_date(get_params['due_to'], 'due_to')
        qs = qs.filter(due_date__lte=due_to)
        parts.append(f'срок до {due_to.isoformat()}')
    if due_from and due_to and due_from > due_to:
        raise ValidationError('due_from не может быть позже due_to', fields={'due_from': 'Должно быть раньше due_to'})

    overdue = get_params.get('overdue')
    if overdue:
        if overdue not in ('0', '1'):
            raise ValidationError('overdue должен быть 0 или 1', fields={'overdue': '0 или 1'})
        today = timezone.localdate()
        if overdue == '1':
            qs = qs.exclude(status=IssueStatus.CLOSED).filter(due_date__lt=today)
            parts.append('только просроченные')
        else:
            qs = qs.filter(Q(status=IssueStatus.CLOSED) | Q(due_date__gte=today))
            parts.append('без просроченных')

    return qs, ', '.join(parts)
