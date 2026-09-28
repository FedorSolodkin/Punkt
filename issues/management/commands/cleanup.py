"""Ежедневная очистка (ТЗ §5.3): запускается планировщиком ОС, а не приложением.

- Черновики бота с истёкшим TTL (24 ч) и их файлы стадирования.
- Файлы стадирования без ссылки в БД старше 24 ч (файлы-сироты).
- Истёкшие и использованные токены входа старше 24 ч.
- Просроченные сессии Django (стандартная clearsessions).
"""
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import LoginToken
from bot.models import BotDraft


class Command(BaseCommand):
    help = 'Ежедневная очистка черновиков, файлов-сирот, токенов и сессий (ТЗ §5.3).'

    def handle(self, *args, **options):
        now = timezone.now()
        cutoff_24h = now - timezone.timedelta(hours=24)

        expired_drafts = list(BotDraft.objects.filter(expires_at__lt=now))
        for draft in expired_drafts:
            staged = draft.payload.get('staged_photo_path')
            if staged:
                path = Path(settings.MEDIA_ROOT) / staged
                path.unlink(missing_ok=True)
        deleted_drafts = len(expired_drafts)
        BotDraft.objects.filter(expires_at__lt=now).delete()

        staging_dir = Path(settings.MEDIA_ROOT) / 'staging'
        orphan_files = 0
        if staging_dir.exists():
            for path in staging_dir.iterdir():
                if path.is_file() and path.stat().st_mtime < cutoff_24h.timestamp():
                    path.unlink(missing_ok=True)
                    orphan_files += 1

        expired_tokens, _ = LoginToken.objects.filter(
            expires_at__lt=cutoff_24h,
        ).delete()
        used_tokens, _ = LoginToken.objects.filter(
            used_at__isnull=False, used_at__lt=cutoff_24h,
        ).delete()

        call_command('clearsessions')

        self.stdout.write(self.style.SUCCESS(
            f'Готово: черновиков удалено {deleted_drafts}, файлов-сирот {orphan_files}, '
            f'токенов удалено {expired_tokens + used_tokens}.',
        ))
