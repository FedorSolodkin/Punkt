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
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bot_draft', verbose_name='пользователь',
    )
    step = models.CharField(
        max_length=20, choices=DraftStep.choices, default=DraftStep.PHOTO, verbose_name='шаг',
    )
    payload = models.JSONField(default=dict, blank=True, verbose_name='данные')
    started_at = models.DateTimeField(auto_now_add=True, verbose_name='начат')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='изменён')
    expires_at = models.DateTimeField(verbose_name='истекает')

    class Meta:
        verbose_name = 'черновик бота'
        verbose_name_plural = 'черновики бота'

    def is_expired(self):
        from django.utils import timezone
        return self.expires_at < timezone.now()


class BotState(models.Model):
    """Единственная строка (id=1) с офсетом long polling (ТЗ §3, §5.1)."""

    id = models.PositiveIntegerField(primary_key=True, default=1)
    next_offset = models.BigIntegerField(default=0, verbose_name='следующий offset')

    class Meta:
        verbose_name = 'состояние бота'
        verbose_name_plural = 'состояние бота'

    @classmethod
    def load(cls):
        state, _ = cls.objects.get_or_create(id=1)
        return state
