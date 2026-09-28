from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, Paginator
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from accounts.models import Role
from core.errors import ApiError, AuthRequired, ValidationError
from core.http import error_response, parse_json_body, require_request_id

from . import services
from .filters import apply_filters
from .photos import validate_and_stage


def _parse_iso_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _bad_filter(request, message):
    return render(
        request, 'issues/registry.html',
        {'error': message, 'issues': [], 'page_obj': None, 'out_of_range': False, 'filters': request.GET},
        status=400,
    )


@login_required
@require_http_methods(['GET'])
def registry(request):
    """GET /issues — реестр с фильтрами (ТЗ §4, §6.2)."""
    actor = request.user
    base_qs = services.visible_issues_queryset(actor)
    has_any_issues = base_qs.exists()

    try:
        qs, filters_text = apply_filters(base_qs, request.GET)
    except ValidationError as exc:
        return _bad_filter(request, exc.message)

    filters_applied = bool(filters_text)
    qs = qs.order_by('-created_at', '-id')

    page_raw = request.GET.get('page', '1')
    try:
        page_number = int(page_raw)
        if page_number < 1:
            raise ValueError
    except ValueError:
        return _bad_filter(request, 'Некорректный номер страницы')

    paginator = Paginator(qs, settings.ISSUES_PAGE_SIZE)
    out_of_range = False
    try:
        page_obj = paginator.page(page_number) if paginator.count else paginator.page(1)
    except EmptyPage:
        # Страница за пределами списка — пустая страница со ссылкой на первую (ТЗ §6.2).
        page_obj = None
        out_of_range = True

    return render(request, 'issues/registry.html', {
        'issues': page_obj.object_list if page_obj else [],
        'page_obj': page_obj,
        'out_of_range': out_of_range,
        'filters': request.GET,
        'has_any_issues': has_any_issues,
        'filters_applied': filters_applied,
        'is_engineer': actor.role == Role.ENGINEER,
        'is_manager_or_engineer': actor.role in (Role.ENGINEER, Role.MANAGER),
    })


@login_required
@require_http_methods(['GET'])
def issue_detail(request, public_id):
    """GET /issues/{uuid} — ТЗ §4."""
    try:
        issue = services.get_issue_for_actor(request.user, public_id)
    except ApiError:
        raise Http404()
    events = issue.events.select_related('actor').all()
    photos = issue.photos.select_related('author').all()
    assignable_users = []
    if request.user.role == Role.ENGINEER and issue.status == 'OPEN':
        User = request.user.__class__
        assignable_users = User.objects.filter(
            site=issue.zone.site, is_active=True, role__isnull=False,
        ).order_by('display_name')
    return render(request, 'issues/detail.html', {
        'issue': issue, 'events': events, 'photos': photos,
        'actor': request.user, 'assignable_users': assignable_users,
    })


@require_http_methods(['POST'])
def issue_submit(request, public_id):
    """POST /issues/{uuid}/submit — multipart (ТЗ §4)."""
    if not request.user.is_authenticated:
        return error_response(AuthRequired('Требуется вход'))
    try:
        request_id = require_request_id(request.POST)
        version_raw = request.POST.get('version')
        if version_raw is None:
            raise ValidationError('Обязательное поле', fields={'version': 'Обязательное поле'})
        try:
            version = int(version_raw)
        except ValueError as exc:
            raise ValidationError('Некорректная версия', fields={'version': 'Целое число'}) from exc

        photo = request.FILES.get('photo')
        if photo is None:
            raise ValidationError('Файл не получен', fields={'photo': 'Обязательное поле'})
        staged = validate_and_stage(photo)

        issue = services.submit_issue(
            actor=request.user, issue_id=public_id, version=version,
            photo_staged=staged, request_id=request_id,
        )
        return JsonResponse({'id': str(issue.public_id), 'status': issue.status, 'version': issue.version})
    except ApiError as exc:
        return error_response(exc)


@require_http_methods(['POST'])
def issue_review(request, public_id):
    """POST /issues/{uuid}/review — JSON (ТЗ §4)."""
    if not request.user.is_authenticated:
        return error_response(AuthRequired('Требуется вход'))
    try:
        data = parse_json_body(request)
        request_id = require_request_id(data)
        version = data.get('version')
        if not isinstance(version, int):
            raise ValidationError('Некорректная версия', fields={'version': 'Целое число'})
        issue = services.review_issue(
            actor=request.user, issue_id=public_id,
            decision=data.get('decision'), reason=data.get('reason'),
            version=version, request_id=request_id,
        )
        return JsonResponse({'id': str(issue.public_id), 'status': issue.status, 'version': issue.version})
    except ApiError as exc:
        return error_response(exc)


@require_http_methods(['POST'])
def issue_edit(request, public_id):
    """POST /issues/{uuid}/edit — JSON (ТЗ §4)."""
    if not request.user.is_authenticated:
        return error_response(AuthRequired('Требуется вход'))
    try:
        data = parse_json_body(request)
        request_id = require_request_id(data)
        version = data.get('version')
        if not isinstance(version, int):
            raise ValidationError('Некорректная версия', fields={'version': 'Целое число'})
        assignee_id = data.get('assignee_id')
        if not assignee_id:
            raise ValidationError('Обязательное поле', fields={'assignee_id': 'Обязательное поле'})
        due_date = _parse_iso_date(data.get('due_date'))
        if due_date is None:
            raise ValidationError('Некорректная дата', fields={'due_date': 'Формат YYYY-MM-DD'})

        issue = services.edit_issue(
            actor=request.user, issue_id=public_id, assignee_id=assignee_id,
            due_date=due_date, reason=data.get('reason'), version=version,
            request_id=request_id,
        )
        return JsonResponse({'id': str(issue.public_id), 'status': issue.status, 'version': issue.version})
    except ApiError as exc:
        return error_response(exc)


@login_required
@require_http_methods(['GET'])
def photo_view(request, photo_id):
    """GET /photos/{uuid} — права соответствуют карточке (ТЗ §4)."""
    try:
        photo = services.get_photo_for_actor(request.user, photo_id)
    except ApiError:
        raise Http404()

    variant = request.GET.get('variant', 'preview')
    if variant not in ('preview', 'original'):
        variant = 'preview'
    rel_path = photo.preview_path if variant == 'preview' else photo.original_path
    full_path = Path(settings.MEDIA_ROOT) / rel_path
    if not full_path.exists():
        raise Http404()
    content_type = 'image/jpeg' if variant == 'preview' else photo.mime
    return FileResponse(open(full_path, 'rb'), content_type=content_type)
