"""Сервисный слой замечаний (ТЗ §2, §3.1, §4.1).

Все мутации проходят здесь и только здесь — веб и бот лишь проверяют
вход и вызывают эти функции (ТЗ §2). Каждая функция:
  1. сначала проверяет request_id на повтор (идемпотентность);
  2. блокирует строку Issue (select_for_update) и сверяет version
     (оптимистичная конкуренция, 409 без записи при несовпадении);
  3. проверяет роль и допустимость перехода статуса;
  4. пишет изменение и IssueEvent одной транзакцией.
"""
import hashlib
import json
import unicodedata
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from core.errors import Conflict, Forbidden, LimitExceeded, NotFound, RateLimited, ValidationError
from django.conf import settings

from .models import Issue, IssueAction, IssueEvent, IssueStatus, Photo, PhotoKind, Zone
from .photos import save_photo

User = get_user_model()


def normalize_text(value) -> str:
    return unicodedata.normalize('NFC', (value or '')).strip()


def compute_request_hash(action: str, fields: dict, photo_sha256: str | None = None) -> str:
    payload = {'action': action, 'fields': fields, 'photo_sha256': photo_sha256}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _find_existing_event(request_id):
    return (
        IssueEvent.objects
        .select_related('issue', 'issue__zone', 'issue__zone__site', 'photo')
        .filter(request_id=request_id)
        .first()
    )


def check_due_date(due_date):
    today = timezone.localdate()
    if not (today <= due_date <= today + timedelta(days=365)):
        raise ValidationError(
            'Некорректный срок',
            fields={'due_date': 'От сегодня до +365 дней'},
        )


def _enforce_change_rate_limit(actor):
    """≤30 успешных изменений в минуту на участника, под блокировкой
    строки User (ТЗ §5.4). Вызывается внутри той же транзакции, что и
    сама мутация, после проверки идемпотентности.
    """
    User.objects.select_for_update().get(pk=actor.pk)
    since = timezone.now() - timedelta(minutes=1)
    recent = IssueEvent.objects.filter(actor=actor, created_at__gte=since).count()
    if recent >= settings.MAX_CHANGES_PER_MINUTE:
        raise RateLimited('Слишком много изменений подряд, повторите позже')


def _resolve_assignee(assignee_id, site):
    assignee = User.objects.filter(
        id=assignee_id, is_active=True, role__isnull=False, site=site,
    ).first()
    if assignee is None:
        raise ValidationError(
            'Исполнитель недоступен',
            fields={'assignee_id': 'Неизвестный или неактивный участник объекта'},
        )
    return assignee


# --- Видимость и доступ -------------------------------------------------

def visible_issues_queryset(actor):
    """Реестр объекта; исполнителю — только назначенные ему карточки (ТЗ §1.1)."""
    qs = Issue.objects.select_related('zone', 'author', 'assignee')
    if actor.role == Role.EXECUTOR:
        return qs.filter(assignee_id=actor.id)
    return qs


def get_issue_for_actor(actor, public_id):
    """Чужая карточка исполнителя скрывается кодом 404 (ТЗ §4.1)."""
    issue = visible_issues_queryset(actor).filter(public_id=public_id).first()
    if issue is None:
        raise NotFound('Замечание не найдено')
    return issue


def get_photo_for_actor(actor, photo_id):
    photo = Photo.objects.select_related('issue').filter(id=photo_id).first()
    if photo is None:
        raise NotFound('Фото не найдено')
    # Права на фото совпадают с правами на карточку (ТЗ §4).
    get_issue_for_actor(actor, photo.issue.public_id)
    return photo


# --- F1: создание замечания (ТЗ §2.1) -----------------------------------

def create_issue(*, actor, zone_id, description, assignee_id, due_date,
                  photo_staged, request_id, draft=None):
    if actor.role != Role.ENGINEER:
        raise Forbidden('Только инженер может создавать замечания')

    description = normalize_text(description)
    if not (1 <= len(description) <= 1000):
        raise ValidationError('Некорректное описание', fields={'description': 'От 1 до 1000 символов'})
    check_due_date(due_date)

    request_hash = compute_request_hash(
        'CREATE',
        {'zone_id': zone_id, 'description': description, 'assignee_id': assignee_id, 'due_date': str(due_date)},
        photo_sha256=photo_staged['sha256'] if photo_staged else None,
    )

    with transaction.atomic():
        existing = _find_existing_event(request_id)
        if existing:
            if existing.request_hash != request_hash:
                raise Conflict('Запрос с таким request_id уже выполнен с другими данными')
            if existing.issue.author_id != actor.id:
                raise Forbidden('Замечание создано другим участником')
            return existing.issue

        _enforce_change_rate_limit(actor)

        if Issue.objects.count() >= settings.MAX_ISSUES:
            raise LimitExceeded('Достигнут лимит числа замечаний на объекте')

        zone = Zone.objects.select_related('site').filter(id=zone_id, active=True).first()
        if zone is None:
            raise ValidationError('Зона недоступна', fields={'zone_id': 'Неизвестная или неактивная зона'})

        assignee = _resolve_assignee(assignee_id, zone.site)

        issue = Issue.objects.create(
            zone=zone,
            author=actor,
            assignee=assignee,
            description=description,
            due_date=due_date,
            status=IssueStatus.OPEN,
            creation_started_at=draft.started_at if draft else timezone.now(),
            draft_id=draft.id if draft else None,
        )

        photo = save_photo(issue=issue, kind=PhotoKind.BEFORE, staged=photo_staged, author=actor)

        IssueEvent.objects.create(
            issue=issue, actor=actor, action=IssueAction.CREATE,
            old_values={},
            new_values={
                'status': issue.status, 'zone_id': zone.id,
                'assignee_id': assignee.id, 'due_date': str(due_date),
            },
            request_id=request_id, request_hash=request_hash,
            result_status=issue.status, result_version=issue.version, photo=photo,
        )
        return issue


# --- F2: отправка на проверку (ТЗ §2.2) ---------------------------------

def submit_issue(*, actor, issue_id, version, photo_staged, request_id):
    request_hash = compute_request_hash(
        'SUBMIT', {'issue_id': str(issue_id), 'version': version},
        photo_sha256=photo_staged['sha256'],
    )

    with transaction.atomic():
        existing = _find_existing_event(request_id)
        if existing:
            if existing.request_hash != request_hash:
                raise Conflict('Запрос с таким request_id уже выполнен с другими данными')
            if existing.issue.assignee_id != actor.id:
                raise Forbidden('Действие доступно только исполнителю замечания')
            return existing.issue

        _enforce_change_rate_limit(actor)

        issue = Issue.objects.select_for_update().filter(public_id=issue_id).first()
        if issue is None or issue.assignee_id != actor.id:
            # Чужая карточка исполнителя — 404, а не 403 (ТЗ §4.1).
            raise NotFound('Замечание не найдено')

        if issue.status != IssueStatus.OPEN:
            raise Conflict('Замечание не в статусе «Открыто»')
        if issue.version != version:
            raise Conflict('Карточка изменилась, обновите её перед повтором')

        after_count = issue.photos.filter(kind=PhotoKind.AFTER).count()
        if after_count >= settings.MAX_AFTER_PHOTOS_PER_ISSUE:
            raise LimitExceeded('Достигнут лимит фотографий исправления на карточку')

        photo = save_photo(issue=issue, kind=PhotoKind.AFTER, staged=photo_staged, author=actor)

        old_status = issue.status
        issue.status = IssueStatus.READY
        issue.version += 1
        issue.save(update_fields=['status', 'version', 'updated_at'])

        IssueEvent.objects.create(
            issue=issue, actor=actor, action=IssueAction.SUBMIT,
            old_values={'status': old_status}, new_values={'status': issue.status},
            request_id=request_id, request_hash=request_hash,
            result_status=issue.status, result_version=issue.version, photo=photo,
        )
        return issue


# --- F2: решение инженера (ТЗ §2.2) -------------------------------------

def review_issue(*, actor, issue_id, decision, reason, version, request_id):
    if decision not in ('accept', 'return'):
        raise ValidationError('Недопустимое решение', fields={'decision': 'accept или return'})

    if decision == 'return':
        reason = normalize_text(reason)
        if not reason:
            raise ValidationError('Укажите причину возврата', fields={'reason': 'Обязательное поле'})
    else:
        reason = None

    request_hash = compute_request_hash(
        'REVIEW', {'issue_id': str(issue_id), 'decision': decision, 'reason': reason, 'version': version},
    )

    with transaction.atomic():
        existing = _find_existing_event(request_id)
        if existing:
            if existing.request_hash != request_hash:
                raise Conflict('Запрос с таким request_id уже выполнен с другими данными')
            if actor.role != Role.ENGINEER:
                raise Forbidden('Проверка доступна только инженеру')
            return existing.issue

        _enforce_change_rate_limit(actor)

        issue = Issue.objects.select_for_update().filter(public_id=issue_id).first()
        if issue is None:
            raise NotFound('Замечание не найдено')
        if actor.role != Role.ENGINEER:
            raise Forbidden('Проверка доступна только инженеру')
        if issue.status != IssueStatus.READY:
            raise Conflict('Замечание не находится на проверке')
        if issue.version != version:
            raise Conflict('Карточка изменилась, обновите её перед повтором')

        old_status = issue.status
        if decision == 'accept':
            issue.status = IssueStatus.CLOSED
            issue.closed_at = timezone.now()
            action = IssueAction.ACCEPT
        else:
            issue.status = IssueStatus.OPEN
            issue.closed_at = None
            action = IssueAction.RETURN
        issue.version += 1
        issue.save(update_fields=['status', 'closed_at', 'version', 'updated_at'])

        IssueEvent.objects.create(
            issue=issue, actor=actor, action=action,
            old_values={'status': old_status}, new_values={'status': issue.status},
            reason=reason, request_id=request_id, request_hash=request_hash,
            result_status=issue.status, result_version=issue.version,
        )
        return issue


# --- Изменение исполнителя/срока (ТЗ §1.1, §3.1) ------------------------

def edit_issue(*, actor, issue_id, assignee_id, due_date, reason, version, request_id):
    reason = normalize_text(reason)
    if not reason:
        raise ValidationError('Укажите причину изменения', fields={'reason': 'Обязательное поле'})

    request_hash = compute_request_hash(
        'EDIT',
        {'issue_id': str(issue_id), 'assignee_id': assignee_id, 'due_date': str(due_date), 'reason': reason, 'version': version},
    )

    with transaction.atomic():
        existing = _find_existing_event(request_id)
        if existing:
            if existing.request_hash != request_hash:
                raise Conflict('Запрос с таким request_id уже выполнен с другими данными')
            if actor.role != Role.ENGINEER:
                raise Forbidden('Изменение доступно только инженеру')
            return existing.issue

        _enforce_change_rate_limit(actor)

        issue = Issue.objects.select_for_update().filter(public_id=issue_id).first()
        if issue is None:
            raise NotFound('Замечание не найдено')
        if actor.role != Role.ENGINEER:
            raise Forbidden('Изменение доступно только инженеру')
        if issue.status != IssueStatus.OPEN:
            raise Conflict('Изменение возможно только для открытого замечания')
        if issue.version != version:
            raise Conflict('Карточка изменилась, обновите её перед повтором')

        check_due_date(due_date)
        assignee = _resolve_assignee(assignee_id, issue.zone.site)

        old_values = {'assignee_id': issue.assignee_id, 'due_date': str(issue.due_date)}
        issue.assignee = assignee
        issue.due_date = due_date
        issue.version += 1
        issue.save(update_fields=['assignee', 'due_date', 'version', 'updated_at'])

        IssueEvent.objects.create(
            issue=issue, actor=actor, action=IssueAction.EDIT,
            old_values=old_values,
            new_values={'assignee_id': assignee.id, 'due_date': str(due_date)},
            reason=reason, request_id=request_id, request_hash=request_hash,
            result_status=issue.status, result_version=issue.version,
        )
        return issue
