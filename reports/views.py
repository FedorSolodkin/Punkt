from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import connection, transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import Role
from core.errors import ApiError, Forbidden, ServiceUnavailable
from core.http import error_response
from issues.filters import apply_filters
from issues.models import IssueStatus, PhotoKind
from issues.services import visible_issues_queryset

from .pdf import PhotoUnavailable, build_pdf


@login_required
@require_http_methods(['GET'])
def report_pdf(request):
    """GET /report.pdf — ТЗ §2.3, §4, §5.3.

    Данные читаются одной транзакцией REPEATABLE READ, PDF не
    сохраняется на диск и отдаётся напрямую из памяти.
    """
    actor = request.user
    try:
        if actor.role not in (Role.ENGINEER, Role.MANAGER):
            raise Forbidden('Отчёт доступен инженеру или руководителю')

        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')

            base_qs = visible_issues_queryset(actor)
            qs, filters_text = apply_filters(base_qs, request.GET)
            qs = qs.order_by('-created_at', '-id').select_related('zone', 'assignee')

            today = timezone.localdate()
            overdue_q = Q(due_date__lt=today) & ~Q(status=IssueStatus.CLOSED)
            counts_qs = qs.aggregate(
                total=Count('id'),
                open=Count('id', filter=Q(status=IssueStatus.OPEN)),
                ready=Count('id', filter=Q(status=IssueStatus.READY)),
                closed=Count('id', filter=Q(status=IssueStatus.CLOSED)),
                overdue=Count('id', filter=overdue_q),
            )

            limited = list(qs.prefetch_related('photos')[:settings.MAX_REPORT_ROWS])

            rows = []
            for issue in limited:
                before = issue.photos.filter(kind=PhotoKind.BEFORE).first()
                after = issue.photos.filter(kind=PhotoKind.AFTER).order_by('-created_at').first()
                rows.append({
                    'number': issue.id,
                    'zone': issue.zone.name,
                    'description': issue.description,
                    'assignee': issue.assignee.display_name or issue.assignee.username,
                    'due_date': issue.due_date.isoformat(),
                    'status_display': IssueStatus(issue.status).label,
                    'overdue': issue.is_overdue,
                    'before_path': str(settings.MEDIA_ROOT / before.preview_path) if before else None,
                    'after_path': str(settings.MEDIA_ROOT / after.preview_path) if after else None,
                })

            try:
                pdf_bytes = build_pdf(
                    site_name=limited[0].zone.site.name if limited else _first_site_name(),
                    generated_at=timezone.localtime().strftime('%d.%m.%Y %H:%M'),
                    filters_text=filters_text,
                    counts=counts_qs,
                    rows=rows,
                )
            except PhotoUnavailable as exc:
                raise ServiceUnavailable('Фото недоступно, отчёт не сформирован') from exc

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="punkt-report.pdf"'
        return response
    except ApiError as exc:
        return error_response(exc)


def _first_site_name():
    from issues.models import Site
    site = Site.objects.first()
    return site.name if site else 'PUNKT'
