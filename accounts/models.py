import hashlib
import secrets

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    ENGINEER = 'ENGINEER', 'Инженер'
    EXECUTOR = 'EXECUTOR', 'Исполнитель'
    MANAGER = 'MANAGER', 'Руководитель'


class User(AbstractUser):
    """Стандартный пользователь Django (ТЗ §3).

    site/telegram_id/role заполнены только для предметных ролей
    (инженер/исполнитель/руководитель); технический администратор —
    обычный суперпользователь Django без этих полей.
    """

    site = models.ForeignKey(
        'issues.Site', null=True, blank=True,
        on_delete=models.PROTECT, related_name='users', verbose_name='объект',
    )
    telegram_id = models.BigIntegerField(
        null=True, blank=True, unique=True, verbose_name='Telegram ID',
        help_text='Участник узнаёт его, отправив боту /start',
    )
    display_name = models.CharField(max_length=100, blank=True, verbose_name='отображаемое имя')
    role = models.CharField(
        max_length=20, choices=Role.choices, null=True, blank=True, verbose_name='предметная роль',
    )

    def __str__(self):
        return self.display_name or self.username

    @property
    def is_engineer(self):
        return self.role == Role.ENGINEER

    @property
    def is_executor(self):
        return self.role == Role.EXECUTOR

    @property
    def is_manager(self):
        return self.role == Role.MANAGER


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


class LoginToken(models.Model):
    """Персональная ссылка входа /web (ТЗ §5.4).

    В базе хранится только SHA-256 хеш случайного токена — не сама
    ссылка, — чтобы утечка БД не давала готовых ссылок для входа.
    """

    TTL_SECONDS = 10 * 60

    token_hash = models.CharField(max_length=64, primary_key=True, verbose_name='хеш токена')
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='login_tokens', verbose_name='пользователь',
    )
    next_path = models.CharField(max_length=255, verbose_name='путь после входа')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='создан')
    expires_at = models.DateTimeField(verbose_name='истекает')
    used_at = models.DateTimeField(null=True, blank=True, verbose_name='использован')

    class Meta:
        verbose_name = 'токен входа'
        verbose_name_plural = 'токены входа'

    @classmethod
    def issue(cls, user, next_path: str) -> str:
        """Создаёт токен и возвращает СЫРОЕ значение (для ссылки)."""
        raw_token = secrets.token_urlsafe(32)
        cls.objects.create(
            token_hash=_hash_token(raw_token),
            user=user,
            next_path=next_path,
            expires_at=timezone.now() + timezone.timedelta(seconds=cls.TTL_SECONDS),
        )
        return raw_token

    @classmethod
    def find_valid(cls, raw_token: str):
        try:
            token = cls.objects.select_related('user').get(token_hash=_hash_token(raw_token))
        except cls.DoesNotExist:
            return None
        if token.used_at is not None or token.expires_at < timezone.now():
            return None
        return token

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=['used_at'])
