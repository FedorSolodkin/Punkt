import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from core.errors import RateLimited

from .models import LoginToken

User = get_user_model()


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def issue_login_token(user, next_path: str) -> str:
    """Выпускает токен /web. ≤5 токенов в минуту на участника, под
    блокировкой строки User (ТЗ §5.4)."""
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        since = timezone.now() - timedelta(minutes=1)
        recent = LoginToken.objects.filter(user=user, created_at__gte=since).count()
        if recent >= settings.MAX_LOGIN_TOKENS_PER_MINUTE:
            raise RateLimited('Слишком много запросов входа подряд, повторите позже')
        return LoginToken.issue(user, next_path)


def safe_next_path(next_path: str) -> str:
    """next_path — только из разрешённого списка (ТЗ §5.4), не открытый редирект."""
    if next_path and any(next_path.startswith(p) for p in settings.LOGIN_ALLOWED_NEXT_PREFIXES):
        return next_path
    return '/issues'


def consume_login_token(raw_token: str):
    """Одно применение токена по POST (ТЗ §5.4).

    Возвращает (user, next_path) или (None, None), если токен
    недействителен, истёк, уже использован или пользователь отключён.
    """
    with transaction.atomic():
        token = (
            LoginToken.objects.select_for_update()
            .select_related('user')
            .filter(token_hash=_hash_token(raw_token))
            .first()
        )
        if token is None or token.used_at is not None or token.expires_at < timezone.now():
            return None, None
        if not token.user.is_active:
            return None, None
        token.mark_used()
        return token.user, token.next_path
