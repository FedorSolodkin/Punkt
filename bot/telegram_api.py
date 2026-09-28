"""Адаптер Telegram Bot API поверх HTTPX (ТЗ §2).

Реализован только использующийся минимум: getUpdates, sendMessage,
getFile, answerCallbackQuery и скачивание файла. Таймауты и повторы —
ТЗ §5.2: подключение 5 с, ответ/скачивание 30 с, long polling 35 с,
три повтора через 1/2/4 с; при 429 — retry_after.
"""
import logging
import time

import httpx
from django.conf import settings

logger = logging.getLogger('punkt.bot')


class TelegramError(Exception):
    def __init__(self, error_code=None, description=None, retry_after=None):
        self.error_code = error_code
        self.description = description
        self.retry_after = retry_after
        super().__init__(f'Telegram error {error_code}: {description}')


class TelegramClient:
    def __init__(self, token=None, api_base=None):
        self.token = token or settings.BOT_TOKEN
        self.api_base = api_base or settings.TELEGRAM_API_BASE
        self._client = httpx.Client(timeout=httpx.Timeout(
            connect=settings.TELEGRAM_CONNECT_TIMEOUT,
            read=settings.TELEGRAM_READ_TIMEOUT,
            write=settings.TELEGRAM_READ_TIMEOUT,
            pool=settings.TELEGRAM_READ_TIMEOUT,
        ))

    def close(self):
        self._client.close()

    def _post(self, method, retries=3, timeout=None, **kwargs):
        url = f'{self.api_base}/bot{self.token}/{method}'
        delay = 1
        for attempt in range(retries + 1):
            try:
                response = self._client.post(url, timeout=timeout, **kwargs)
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt >= retries:
                    raise
                logger.warning('Telegram %s network error (попытка %s): %s', method, attempt + 1, exc)
                time.sleep(delay)
                delay *= 2
                continue

            if data.get('ok'):
                return data.get('result')

            error_code = data.get('error_code')
            description = data.get('description')
            retry_after = (data.get('parameters') or {}).get('retry_after')
            if error_code == 429 and retry_after is not None and attempt < retries:
                time.sleep(retry_after)
                continue
            if error_code and error_code >= 500 and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise TelegramError(error_code, description, retry_after)
        raise TelegramError(None, 'Исчерпаны попытки запроса к Telegram')

    def get_updates(self, offset, timeout=None):
        poll_timeout = settings.TELEGRAM_POLL_TIMEOUT if timeout is None else timeout
        http_timeout = httpx.Timeout(
            connect=settings.TELEGRAM_CONNECT_TIMEOUT,
            read=poll_timeout + 5, write=10, pool=10,
        )
        return self._post(
            'getUpdates', retries=0, timeout=http_timeout,
            json={
                'offset': offset, 'timeout': poll_timeout,
                'allowed_updates': ['message', 'callback_query'],
            },
        )

    def send_message(self, chat_id, text, reply_markup=None):
        payload = {'chat_id': chat_id, 'text': text}
        if reply_markup is not None:
            payload['reply_markup'] = reply_markup
        return self._post('sendMessage', json=payload)

    def answer_callback_query(self, callback_query_id, text=None):
        payload = {'callback_query_id': callback_query_id}
        if text:
            payload['text'] = text
        return self._post('answerCallbackQuery', json=payload)

    def get_file(self, file_id):
        return self._post('getFile', json={'file_id': file_id})

    def download_file(self, file_path):
        url = f'{self.api_base}/file/bot{self.token}/{file_path}'
        delay = 1
        for attempt in range(4):
            try:
                response = self._client.get(url)
                response.raise_for_status()
                return response.content
            except httpx.HTTPError:
                if attempt >= 3:
                    raise
                time.sleep(delay)
                delay *= 2
