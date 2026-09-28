"""Создаёт единственный объект пилота, если его ещё нет (ТЗ §1: «объект
подставляется автоматически»). Остальная настройка — зоны, участники,
роли — через Django Admin."""
from django.core.management.base import BaseCommand

from issues.models import Site


class Command(BaseCommand):
    help = 'Создаёт объект (Site) пилота, если он ещё не создан.'

    def add_arguments(self, parser):
        parser.add_argument('--name', default='Объект PUNKT')
        parser.add_argument('--timezone', default='Asia/Novosibirsk')

    def handle(self, *args, **options):
        if Site.objects.exists():
            site = Site.objects.first()
            self.stdout.write(f'Объект уже существует: {site.name} (id={site.id})')
            return
        site = Site.objects.create(name=options['name'], timezone=options['timezone'])
        self.stdout.write(self.style.SUCCESS(f'Создан объект «{site.name}» (id={site.id})'))
