"""Один процесс runbot, long polling (ТЗ §2, §5.1)."""
import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from bot.conversation import deliver, handle_update
from bot.models import BotState
from bot.telegram_api import TelegramClient, TelegramError

logger = logging.getLogger('punkt.bot')


class Command(BaseCommand):
    help = 'Запускает long-polling обработчик Telegram-бота PUNKT.'

    def handle(self, *args, **options):
        if not settings.BOT_TOKEN:
            self.stderr.write(self.style.ERROR('BOT_TOKEN не задан в .env — runbot остановлен'))
            return

        BotState.load()
        client = TelegramClient()
        try:
            # Команда /start видна в кнопке-меню Telegram даже до того,
            # как администратор привязал участника (ТЗ §5.1).
            client.set_my_commands([{'command': 'start', 'description': 'Начать / меню'}])
        except TelegramError:
            logger.exception('Не удалось задать команды по умолчанию')
        self.stdout.write('runbot: запущен, long polling...')
        try:
            while True:
                self._poll_once(client)
        except KeyboardInterrupt:
            self.stdout.write('runbot: остановлен')
        finally:
            client.close()

    def _poll_once(self, client):
        state = BotState.load()
        try:
            updates = client.get_updates(state.next_offset)
        except TelegramError as exc:
            logger.warning('getUpdates не удался: %s', exc)
            time.sleep(3)
            return
        except Exception:
            logger.exception('Неожиданная ошибка long polling')
            time.sleep(3)
            return

        for update in updates:
            self._process_update(client, update)

    def _process_update(self, client, update):
        current = BotState.load()
        if update['update_id'] < current.next_offset:
            return  # старое обновление пропускается (ТЗ §5.1)

        try:
            with transaction.atomic():
                actions = handle_update(update)
                state = BotState.objects.select_for_update().get(pk=1)
                state.next_offset = update['update_id'] + 1
                state.save(update_fields=['next_offset'])
        except Exception:
            # При сбое offset не меняется — Telegram пришлёт то же update снова (ТЗ §5.1).
            logger.exception('Ошибка обработки update_id=%s, offset не продвинут', update.get('update_id'))
            return

        deliver(client, actions)
