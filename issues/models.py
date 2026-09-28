import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Site(models.Model):
    """Строительный объект. В пилоте — ровно одна запись (ТЗ §3)."""

    name = models.CharField(max_length=120, verbose_name='название')
    timezone = models.CharField(max_length=64, default='Asia/Novosibirsk', verbose_name='часовой пояс')

    class Meta:
        verbose_name = 'объект'
        verbose_name_plural = 'объекты'

    def __str__(self):
        return self.name


class Zone(models.Model):
    """Зона объекта. Использованная зона не удаляется — только active=False."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='zones', verbose_name='объект')
    name = models.CharField(max_length=100, verbose_name='название')
    active = models.BooleanField(default=True, verbose_name='активна')

    class Meta:
        verbose_name = 'зона'
        verbose_name_plural = 'зоны'
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

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, verbose_name='публичный ID')
    zone = models.ForeignKey(Zone, on_delete=models.PROTECT, related_name='issues', verbose_name='зона')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='authored_issues',
        verbose_name='автор',
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assigned_issues',
        verbose_name='исполнитель',
    )
    description = models.CharField(max_length=1000, verbose_name='описание')
    due_date = models.DateField(verbose_name='срок')
    status = models.CharField(
        max_length=10, choices=IssueStatus.choices, default=IssueStatus.OPEN, verbose_name='статус',
    )
    version = models.PositiveIntegerField(default=1, verbose_name='версия')

    # Момент, когда автор начал заполнять карточку в боте (черновик),
    # используется для замера времени создания (ТЗ §8.2).
    creation_started_at = models.DateTimeField(verbose_name='начало создания')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='изменено')
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name='закрыто')

    # Черновик бота, из которого создана карточка — используется для
    # детерминированного request_id при повторном подтверждении (ТЗ §3.1).
    draft_id = models.UUIDField(null=True, blank=True, unique=True, verbose_name='ID черновика')

    class Meta:
        verbose_name = 'замечание'
        verbose_name_plural = 'замечания'
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
    issue = models.ForeignKey(Issue, on_delete=models.PROTECT, related_name='photos', verbose_name='замечание')
    kind = models.CharField(max_length=10, choices=PhotoKind.choices, verbose_name='вид')
    original_path = models.CharField(max_length=255, unique=True, verbose_name='путь к оригиналу')
    preview_path = models.CharField(max_length=255, unique=True, verbose_name='путь к превью')
    mime = models.CharField(max_length=50, verbose_name='MIME-тип')
    size_bytes = models.PositiveIntegerField(verbose_name='размер, байт')
    sha256 = models.CharField(max_length=64, verbose_name='SHA-256')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', verbose_name='автор',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='создано')

    class Meta:
        verbose_name = 'фотография'
        verbose_name_plural = 'фотографии'
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

    issue = models.ForeignKey(
        Issue, on_delete=models.PROTECT, related_name='events', verbose_name='замечание',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', verbose_name='участник',
    )
    action = models.CharField(max_length=10, choices=IssueAction.choices, verbose_name='действие')
    old_values = models.JSONField(default=dict, blank=True, verbose_name='было')
    new_values = models.JSONField(default=dict, blank=True, verbose_name='стало')
    reason = models.CharField(max_length=500, null=True, blank=True, verbose_name='причина')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='создано')

    request_id = models.UUIDField(unique=True, verbose_name='request_id')
    request_hash = models.CharField(max_length=64, verbose_name='хеш запроса')

    # Сохранённый результат для повторного ответа при том же request_id
    # без повторного выполнения бизнес-логики (ТЗ §3.1).
    result_status = models.CharField(
        max_length=10, choices=IssueStatus.choices, null=True, blank=True, verbose_name='итоговый статус',
    )
    result_version = models.PositiveIntegerField(null=True, blank=True, verbose_name='итоговая версия')
    photo = models.ForeignKey(
        Photo, on_delete=models.PROTECT, null=True, blank=True, related_name='+', verbose_name='фото',
    )

    class Meta:
        verbose_name = 'событие замечания'
        verbose_name_plural = 'события замечаний'
        indexes = [
            models.Index(fields=['issue', 'created_at']),
        ]
        ordering = ['created_at']

    def __str__(self):
        return f'{self.action} on issue #{self.issue_id}'
