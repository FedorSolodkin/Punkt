"""Демоданные для локальной проверки без Telegram: объект, зона, три
роли и одноразовые ссылки входа. Только для разработки — не запускать
в проде. Запуск: python scripts/seed_dev.py (из корня проекта, venv активен)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django

django.setup()

from django.contrib.auth import get_user_model

from accounts.models import Role
from accounts.services import issue_login_token
from issues.models import Site, Zone

User = get_user_model()

site, _ = Site.objects.get_or_create(name='ЖК Северный', defaults={'timezone': 'Asia/Novosibirsk'})
zone, _ = Zone.objects.get_or_create(site=site, name='Секция 1, этаж 3', defaults={'active': True})

if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', password='admin12345')
    print('superuser admin/admin12345 создан')

engineer, created = User.objects.get_or_create(
    username='engineer1', defaults=dict(
        site=site, role=Role.ENGINEER, display_name='Иван Инженеров', telegram_id=111111,
    ),
)
if created:
    engineer.set_unusable_password()
    engineer.save()

executor, created = User.objects.get_or_create(
    username='executor1', defaults=dict(
        site=site, role=Role.EXECUTOR, display_name='Пётр Исполнителев', telegram_id=222222,
    ),
)
if created:
    executor.set_unusable_password()
    executor.save()

manager, created = User.objects.get_or_create(
    username='manager1', defaults=dict(
        site=site, role=Role.MANAGER, display_name='Мария Руководителева', telegram_id=333333,
    ),
)
if created:
    manager.set_unusable_password()
    manager.save()

for u in (engineer, executor, manager):
    token = issue_login_token(u, '/issues')
    print(f'{u.display_name} ({u.role}): http://127.0.0.1:8000/login?t={token}')
