import uuid

from django.conf import settings
from django.db import models


class DraftStep(models.TextChoices):
    PHOTO = 'PHOTO', 'Фото'
    ZONE = 'ZONE', 'Зона'
    DESCRIPTION = 'DESCRIPTION', 'Описание'
    ASSIGNEE = 'ASSIGNEE', 'Исполнитель'
    DUE_DATE = 'DUE_DATE', 'Срок'
    CONFIRM = 'CONFIRM', 'Подтверждение'


class BotDraft(models.Model):
    """Черновик создания замечания в диалоге бота (ТЗ §3, §5.1).

    payload постепенно заполняется по шагам: zone_id, description,
    assignee_id, due_date, staged_photo_path.
    """

    TTL_HOURS = 24

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bot_draft')
    step = models.CharField(max_length=20, choices=DraftStep.choices, default=DraftStep.PHOTO)
    payload = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    def is_expired(self):
        from django.utils import timezone
        return self.expires_at < timezone.now()


class BotState(models.Model):
    """Единственная строка (id=1) с офсетом long polling (ТЗ §3, §5.1)."""

    id = models.PositiveIntegerField(primary_key=True, default=1)
    next_offset = models.BigIntegerField(default=0)

    @classmethod
    def load(cls):
        state, _ = cls.objects.get_or_create(id=1)
        return state
