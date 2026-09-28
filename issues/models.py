import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Site(models.Model):
    """Строительный объект. В пилоте — ровно одна запись (ТЗ §3)."""

    name = models.CharField(max_length=120)
    timezone = models.CharField(max_length=64, default='Asia/Novosibirsk')

    def __str__(self):
        return self.name


class Zone(models.Model):
    """Зона объекта. Использованная зона не удаляется — только active=False."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='zones')
    name = models.CharField(max_length=100)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['site', 'name'], name='uq_zone_site_name'),
        ]

    def __str__(self):
        return self.name


class IssueStatus(models.TextChoices):
    OPEN = 'OPEN', 'Открыто'
    READY = 'READY', 'На проверке'
    CLOSED = 'CLOSED', 'Закрыто'


class Issue(models.Model):
    """Карточка замечания. id — он же номер карточки (ТЗ §3)."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    zone = models.ForeignKey(Zone, on_delete=models.PROTECT, related_name='issues')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='authored_issues',
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assigned_issues',
    )
    description = models.CharField(max_length=1000)
    due_date = models.DateField()
    status = models.CharField(max_length=10, choices=IssueStatus.choices, default=IssueStatus.OPEN)
    version = models.PositiveIntegerField(default=1)

    # Момент, когда автор начал заполнять карточку в боте (черновик),
    # используется для замера времени создания (ТЗ §8.2).
    creation_started_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    # Черновик бота, из которого создана карточка — используется для
    # детерминированного request_id при повторном подтверждении (ТЗ §3.1).
    draft_id = models.UUIDField(null=True, blank=True, unique=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'due_date']),
            models.Index(fields=['assignee', 'status']),
        ]
        constraints = [
            # closed_at заполнен тогда и только тогда, когда статус CLOSED (ТЗ §3.1).
            models.CheckConstraint(
                check=(
                    (Q(status=IssueStatus.CLOSED) & Q(closed_at__isnull=False))
                    | (~Q(status=IssueStatus.CLOSED) & Q(closed_at__isnull=True))
                ),
                name='closed_at_matches_status',
            ),
        ]

    def __str__(self):
        return f'#{self.id} {self.description[:40]}'

    @property
    def is_overdue(self):
        from django.utils import timezone as tz
        return self.status != IssueStatus.CLOSED and self.due_date < tz.localdate()


class PhotoKind(models.TextChoices):
    BEFORE = 'BEFORE', 'До'
    AFTER = 'AFTER', 'После'


class Photo(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(Issue, on_delete=models.PROTECT, related_name='photos')
    kind = models.CharField(max_length=10, choices=PhotoKind.choices)
    original_path = models.CharField(max_length=255, unique=True)
    preview_path = models.CharField(max_length=255, unique=True)
    mime = models.CharField(max_length=50)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Ровно одно фото BEFORE на карточку (ТЗ §3.1).
            models.UniqueConstraint(
                fields=['issue'], condition=Q(kind='BEFORE'), name='uq_one_before_photo_per_issue',
            ),
        ]

    def __str__(self):
        return f'{self.kind} photo for issue #{self.issue_id}'


class IssueAction(models.TextChoices):
    CREATE = 'CREATE', 'Создание'
    SUBMIT = 'SUBMIT', 'Отправка на проверку'
    ACCEPT = 'ACCEPT', 'Принятие'
    RETURN = 'RETURN', 'Возврат'
    EDIT = 'EDIT', 'Изменение'


class IssueEvent(models.Model):
    """Запись истории и опора идемпотентности (ТЗ §3.1, §4.1)."""

    issue = models.ForeignKey(Issue, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    action = models.CharField(max_length=10, choices=IssueAction.choices)
    old_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    request_id = models.UUIDField(unique=True)
    request_hash = models.CharField(max_length=64)

    # Сохранённый результат для повторного ответа при том же request_id
    # без повторного выполнения бизнес-логики (ТЗ §3.1).
    result_status = models.CharField(max_length=10, choices=IssueStatus.choices, null=True, blank=True)
    result_version = models.PositiveIntegerField(null=True, blank=True)
    photo = models.ForeignKey(Photo, on_delete=models.PROTECT, null=True, blank=True, related_name='+')

    class Meta:
        indexes = [
            models.Index(fields=['issue', 'created_at']),
        ]
        ordering = ['created_at']

    def __str__(self):
        return f'{self.action} on issue #{self.issue_id}'
