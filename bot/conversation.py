"""Диалог создания замечания в боте (ТЗ §5.1).

handle_update() вызывается внутри одной транзакции БД вместе с
продвижением offset (ТЗ §5.1: «next_offset и результат фиксируются
одной транзакцией»). Она НЕ отправляет сообщения сама — возвращает
список действий (SendMessage/AnswerCallback), которые runbot выполняет
уже после успешной фиксации транзакции («ответ после фиксации»).
"""
import io
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Role
from accounts.services import issue_login_token
from core.errors import ApiError
from issues import services as issue_services
from issues.models import Zone
from issues.photos import validate_and_stage

from .models import BotDraft, DraftStep
from .telegram_api import TelegramClient, TelegramError

User = get_user_model()

STEP_ORDER = [
    DraftStep.PHOTO, DraftStep.ZONE, DraftStep.DESCRIPTION,
    DraftStep.ASSIGNEE, DraftStep.DUE_DATE, DraftStep.CONFIRM,
]


class SendMessage:
    def __init__(self, chat_id, text, buttons=None):
        self.chat_id = chat_id
        self.text = text
        self.reply_markup = {'inline_keyboard': buttons} if buttons else None


class AnswerCallback:
    def __init__(self, callback_query_id, text=None):
        self.callback_query_id = callback_query_id
        self.text = text


def handle_update(update: dict) -> list:
    if 'callback_query' in update:
        return _handle_callback(update['callback_query'])
    if 'message' in update:
        return _handle_message(update['message'])
    return []


def deliver(client: TelegramClient, actions: list):
    for action in actions:
        try:
            if isinstance(action, SendMessage):
                client.send_message(action.chat_id, action.text, action.reply_markup)
            elif isinstance(action, AnswerCallback):
                client.answer_callback_query(action.callback_query_id, action.text)
        except TelegramError:
            # Сбой ответа не отменяет уже зафиксированную запись (ТЗ §5.1).
            import logging
            logging.getLogger('punkt.bot').exception('Не удалось отправить ответ Telegram')


# --- Сообщения -----------------------------------------------------------

def _handle_message(message):
    chat = message.get('chat') or {}
    if chat.get('type') != 'private':
        return []  # групповые сообщения игнорируются (ТЗ §2)
    from_id = (message.get('from') or {}).get('id')
    if from_id is None:
        return []
    chat_id = chat['id']

    user = User.objects.filter(telegram_id=from_id, is_active=True).first()
    if user is None:
        return [SendMessage(
            chat_id,
            f'Ваш Telegram ID: {from_id}.\n'
            'Он не привязан ни к одному участнику объекта PUNKT. '
            'Передайте этот ID администратору, чтобы получить доступ.',
        )]

    text = (message.get('text') or '').strip()
    if text.startswith('/'):
        command = text.split()[0].split('@')[0].lower()
        return _handle_command(user, chat_id, command)

    draft = BotDraft.objects.filter(user=user).first()
    if draft is None or draft.is_expired():
        if draft is not None:
            draft.delete()
        return [SendMessage(chat_id, 'Нет активного черновика. /new — создать замечание, /start — меню.')]

    return _handle_draft_input(user, chat_id, draft, message)


def _handle_command(user, chat_id, command):
    if command == '/start':
        return [SendMessage(chat_id, _menu_text(user), _menu_buttons(user))]
    if command == '/web':
        return _handle_web(user, chat_id)
    if command == '/new':
        return _handle_new(user, chat_id)
    if command == '/resume':
        return _handle_resume(user, chat_id)
    if command == '/cancel':
        return _handle_cancel(user, chat_id)
    return [SendMessage(chat_id, 'Неизвестная команда. /start — меню.', _menu_buttons(user))]


def _menu_text(user):
    lines = ['PUNKT — меню:', '/web — ссылка для входа на сайт']
    if user.role == Role.ENGINEER:
        lines += [
            '/new — создать замечание',
            '/resume — продолжить черновик или последняя карточка',
            '/cancel — отменить черновик',
        ]
    return '\n'.join(lines)


def _menu_buttons(user):
    """Те же команды, но кнопками — не набирать вручную (ТЗ §5.1)."""
    rows = [[{'text': '🌐 Открыть сайт', 'callback_data': 'menu:web'}]]
    if user.role == Role.ENGINEER:
        rows.append([{'text': '🆕 Новое замечание', 'callback_data': 'menu:new'}])
        rows.append([
            {'text': '▶️ Продолжить черновик', 'callback_data': 'menu:resume'},
            {'text': '❌ Отменить черновик', 'callback_data': 'menu:cancel'},
        ])
    return rows


def _handle_web(user, chat_id):
    try:
        raw_token = issue_login_token(user, '/issues')
    except ApiError as exc:
        return [SendMessage(chat_id, exc.message)]
    link = f'{settings.BASE_URL}/login?t={raw_token}'
    return [SendMessage(chat_id, f'Ссылка для входа (10 минут, одно применение):\n{link}')]


def _handle_new(user, chat_id):
    if user.role != Role.ENGINEER:
        return [SendMessage(chat_id, 'Создание замечаний доступно только инженеру.')]
    draft = BotDraft.objects.filter(user=user).first()
    if draft is not None and not draft.is_expired():
        buttons = [[
            {'text': 'Продолжить', 'callback_data': f'{draft.id.hex}|resume|'},
            {'text': 'Отменить', 'callback_data': f'{draft.id.hex}|cancel|'},
        ]]
        return [SendMessage(chat_id, 'У вас есть незавершённый черновик. Продолжить или отменить?', buttons)]
    if draft is not None:
        draft.delete()
    draft = BotDraft.objects.create(
        user=user, step=DraftStep.PHOTO, payload={},
        expires_at=timezone.now() + timedelta(hours=BotDraft.TTL_HOURS),
    )
    return [SendMessage(chat_id, 'Отправьте одно фото дефекта (JPEG или PNG, до 10 МиБ).', _nav_buttons(draft, back=False))]


def _handle_resume(user, chat_id):
    if user.role != Role.ENGINEER:
        return [SendMessage(chat_id, 'Доступно только инженеру.')]
    draft = BotDraft.objects.filter(user=user).first()
    if draft is not None and not draft.is_expired():
        return [SendMessage(chat_id, _step_prompt_text(draft), _step_buttons(draft))]
    if draft is not None:
        draft.delete()
    last_issue = user.authored_issues.order_by('-created_at').first()
    if last_issue is None:
        return [SendMessage(chat_id, 'Черновиков и карточек пока нет. Отправьте /new.')]
    link = f'{settings.BASE_URL}/issues/{last_issue.public_id}'
    return [SendMessage(
        chat_id,
        f'Ваше последнее замечание: #{last_issue.id} ({last_issue.get_status_display()}).\n{link}',
    )]


def _handle_cancel(user, chat_id):
    draft = BotDraft.objects.filter(user=user).first()
    if draft is None:
        return [SendMessage(chat_id, 'Нет активного черновика.')]
    draft.delete()
    return [SendMessage(chat_id, 'Черновик отменён.')]


# --- Шаги черновика --------------------------------------------------------

def _handle_draft_input(user, chat_id, draft, message):
    if draft.step == DraftStep.PHOTO:
        return _handle_photo_step(chat_id, draft, message)
    if draft.step == DraftStep.DESCRIPTION:
        return _handle_description_step(chat_id, draft, message)
    if draft.step == DraftStep.DUE_DATE:
        return _handle_due_date_step(chat_id, draft, message)
    return [SendMessage(chat_id, 'Пожалуйста, воспользуйтесь кнопками выше.', _step_buttons(draft))]


def _handle_photo_step(chat_id, draft, message):
    if message.get('media_group_id'):
        return [SendMessage(chat_id, 'Альбомы не поддерживаются. Отправьте одно фото.', _nav_buttons(draft, back=False))]
    if message.get('video') or message.get('voice') or message.get('video_note'):
        return [SendMessage(chat_id, 'Видео и голосовые сообщения не поддерживаются. Отправьте одно фото.', _nav_buttons(draft, back=False))]

    file_id = None
    photos = message.get('photo')
    if photos:
        file_id = photos[-1]['file_id']
    elif message.get('document') and (message['document'].get('mime_type') or '').startswith('image/'):
        file_id = message['document']['file_id']
    if file_id is None:
        return [SendMessage(chat_id, 'Отправьте одно фото (JPEG или PNG).', _nav_buttons(draft, back=False))]

    client = TelegramClient()
    try:
        file_info = client.get_file(file_id)
        data = client.download_file(file_info['file_path'])
    except TelegramError:
        return [SendMessage(chat_id, 'Не удалось получить файл из Telegram. Попробуйте ещё раз.', _nav_buttons(draft, back=False))]
    finally:
        client.close()

    try:
        staged = validate_and_stage(io.BytesIO(data))
    except ApiError as exc:
        return [SendMessage(chat_id, exc.message, _nav_buttons(draft, back=False))]

    staging_dir = Path(settings.MEDIA_ROOT) / 'staging'
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / f'{draft.id.hex}.{staged["extension"]}'
    staged_path.write_bytes(staged['data'])

    draft.payload['staged_photo_path'] = str(staged_path.relative_to(settings.MEDIA_ROOT)).replace('\\', '/')
    draft.step = DraftStep.ZONE
    draft.save(update_fields=['payload', 'step', 'updated_at'])

    zones = list(Zone.objects.filter(active=True).order_by('name'))
    if not zones:
        return [SendMessage(chat_id, 'На объекте нет активных зон. Обратитесь к администратору.')]
    return [SendMessage(chat_id, 'Выберите зону:', _zone_buttons(draft, zones))]


def _handle_description_step(chat_id, draft, message):
    normalized = issue_services.normalize_text(message.get('text') or '')
    if not (1 <= len(normalized) <= 1000):
        return [SendMessage(chat_id, 'Описание должно быть от 1 до 1000 символов. Повторите ввод.', _nav_buttons(draft))]
    draft.payload['description'] = normalized
    draft.step = DraftStep.ASSIGNEE
    draft.save(update_fields=['payload', 'step', 'updated_at'])

    site = _draft_site(draft)
    users = list(User.objects.filter(site=site, is_active=True, role__isnull=False).order_by('display_name'))
    if not users:
        return [SendMessage(chat_id, 'На объекте нет активных участников. Обратитесь к администратору.')]
    return [SendMessage(chat_id, 'Выберите исполнителя:', _assignee_buttons(draft, users))]


def _handle_due_date_step(chat_id, draft, message):
    text = (message.get('text') or '').strip()
    try:
        due_date = datetime.strptime(text, '%d.%m.%Y').date()
    except ValueError:
        return [SendMessage(chat_id, 'Неверный формат. Введите срок как ДД.ММ.ГГГГ, например 31.12.2026.', _nav_buttons(draft))]
    try:
        issue_services.check_due_date(due_date)
    except ApiError as exc:
        return [SendMessage(chat_id, exc.message, _nav_buttons(draft))]
    draft.payload['due_date'] = due_date.isoformat()
    draft.step = DraftStep.CONFIRM
    draft.save(update_fields=['payload', 'step', 'updated_at'])
    return [SendMessage(chat_id, _confirm_text(draft), _confirm_buttons(draft))]


# --- Callback-кнопки -------------------------------------------------------

def _handle_callback(callback_query):
    data = callback_query.get('data', '')
    from_id = (callback_query.get('from') or {}).get('id')
    chat = (callback_query.get('message') or {}).get('chat') or {}
    callback_id = callback_query['id']
    chat_id = chat.get('id')

    user = User.objects.filter(telegram_id=from_id, is_active=True).first()
    if user is None or chat_id is None:
        return [AnswerCallback(callback_id, 'Доступ не найден')]

    if data.startswith('menu:'):
        command = '/' + data.split(':', 1)[1]
        return [AnswerCallback(callback_id)] + _handle_command(user, chat_id, command)

    parts = (data.split('|', 2) + ['', ''])[:3]
    draft_hex, action, value = parts

    draft = BotDraft.objects.filter(user=user).first()
    if draft is None or draft.id.hex != draft_hex or draft.is_expired():
        return [AnswerCallback(callback_id, 'Черновик устарел'),
                SendMessage(chat_id, 'Черновик устарел или отменён. Отправьте /new.')]

    if action == 'cancel':
        draft.delete()
        return [AnswerCallback(callback_id), SendMessage(chat_id, 'Черновик отменён.')]

    if action == 'resume':
        return [AnswerCallback(callback_id), SendMessage(chat_id, _step_prompt_text(draft), _step_buttons(draft))]

    if action == 'back':
        _go_back(draft)
        return [AnswerCallback(callback_id), SendMessage(chat_id, _step_prompt_text(draft), _step_buttons(draft))]

    if draft.step == DraftStep.ZONE and action == 'zone':
        zone = Zone.objects.filter(id=value, active=True).first()
        if zone is None:
            return [AnswerCallback(callback_id, 'Зона недоступна')]
        draft.payload['zone_id'] = zone.id
        draft.step = DraftStep.DESCRIPTION
        draft.save(update_fields=['payload', 'step', 'updated_at'])
        return [AnswerCallback(callback_id),
                SendMessage(chat_id, 'Опишите замечание текстом (1–1000 символов).', _nav_buttons(draft))]

    if draft.step == DraftStep.ASSIGNEE and action == 'assignee':
        site = _draft_site(draft)
        assignee = User.objects.filter(id=value, site=site, is_active=True, role__isnull=False).first()
        if assignee is None:
            return [AnswerCallback(callback_id, 'Исполнитель недоступен')]
        draft.payload['assignee_id'] = assignee.id
        draft.step = DraftStep.DUE_DATE
        draft.save(update_fields=['payload', 'step', 'updated_at'])
        return [AnswerCallback(callback_id),
                SendMessage(chat_id, 'Введите срок как ДД.ММ.ГГГГ (от сегодня до +365 дней).', _nav_buttons(draft))]

    if draft.step == DraftStep.CONFIRM and action == 'confirm':
        return _confirm_draft(user, chat_id, draft, callback_id)

    return [AnswerCallback(callback_id, 'Действие недоступно на этом шаге')]


def _confirm_draft(user, chat_id, draft, callback_id):
    payload = draft.payload
    staged_path = payload.get('staged_photo_path')
    if not staged_path:
        draft.delete()
        return [AnswerCallback(callback_id), SendMessage(chat_id, 'Фото потеряно, начните заново: /new')]
    full_path = Path(settings.MEDIA_ROOT) / staged_path
    if not full_path.exists():
        draft.delete()
        return [AnswerCallback(callback_id), SendMessage(chat_id, 'Фото недоступно, начните заново: /new')]

    try:
        with open(full_path, 'rb') as fh:
            staged = validate_and_stage(fh)
        due_date = date.fromisoformat(payload['due_date'])
        # Детерминированный request_id из draft_id: повторное подтверждение
        # не создаёт вторую карточку (ТЗ §3.1).
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'punkt-draft:{draft.id}'))
        issue = issue_services.create_issue(
            actor=user, zone_id=payload.get('zone_id'), description=payload.get('description', ''),
            assignee_id=payload.get('assignee_id'), due_date=due_date, photo_staged=staged,
            request_id=request_id, draft=draft,
        )
    except ApiError as exc:
        # Шаг сохраняется, предлагается повтор (ТЗ §5.2).
        return [AnswerCallback(callback_id), SendMessage(chat_id, exc.message, _confirm_buttons(draft))]
    except (KeyError, ValueError):
        return [AnswerCallback(callback_id), SendMessage(chat_id, 'Данные черновика повреждены, начните заново: /new')]

    try:
        full_path.unlink(missing_ok=True)
    except OSError:
        pass
    draft.delete()

    link = f'{settings.BASE_URL}/issues/{issue.public_id}'
    buttons = [[{'text': 'Открыть', 'url': link}, {'text': 'Новое замечание', 'callback_data': 'menu:new'}]]
    return [AnswerCallback(callback_id), SendMessage(chat_id, f'Замечание #{issue.id} создано.\n{link}', buttons)]


# --- Вспомогательные функции -----------------------------------------------

def _draft_site(draft):
    zone = Zone.objects.select_related('site').filter(id=draft.payload.get('zone_id')).first()
    return zone.site if zone else None


def _go_back(draft):
    idx = STEP_ORDER.index(draft.step)
    if idx == 0:
        return
    draft.step = STEP_ORDER[idx - 1]
    draft.save(update_fields=['step', 'updated_at'])


def _nav_buttons(draft, back=True):
    row = []
    if back:
        row.append({'text': '« Назад', 'callback_data': f'{draft.id.hex}|back|'})
    row.append({'text': 'Отмена', 'callback_data': f'{draft.id.hex}|cancel|'})
    return [row]


def _zone_buttons(draft, zones):
    rows = [[{'text': z.name, 'callback_data': f'{draft.id.hex}|zone|{z.id}'}] for z in zones]
    rows.append([{'text': 'Отмена', 'callback_data': f'{draft.id.hex}|cancel|'}])
    return rows


def _assignee_buttons(draft, users):
    rows = [[{'text': u.display_name or u.username, 'callback_data': f'{draft.id.hex}|assignee|{u.id}'}] for u in users]
    rows.append([{'text': '« Назад', 'callback_data': f'{draft.id.hex}|back|'},
                 {'text': 'Отмена', 'callback_data': f'{draft.id.hex}|cancel|'}])
    return rows


def _confirm_text(draft):
    zone = Zone.objects.filter(id=draft.payload.get('zone_id')).first()
    assignee = User.objects.filter(id=draft.payload.get('assignee_id')).first()
    return (
        'Проверьте данные:\n'
        f'Зона: {zone.name if zone else "?"}\n'
        f'Описание: {draft.payload.get("description", "")}\n'
        f'Исполнитель: {(assignee.display_name or assignee.username) if assignee else "?"}\n'
        f'Срок: {draft.payload.get("due_date", "")}\n\n'
        'Подтвердить создание замечания?'
    )


def _confirm_buttons(draft):
    return [[
        {'text': '✅ Подтвердить', 'callback_data': f'{draft.id.hex}|confirm|'},
        {'text': '« Назад', 'callback_data': f'{draft.id.hex}|back|'},
        {'text': 'Отмена', 'callback_data': f'{draft.id.hex}|cancel|'},
    ]]


def _step_prompt_text(draft):
    if draft.step == DraftStep.CONFIRM:
        return _confirm_text(draft)
    texts = {
        DraftStep.PHOTO: 'Отправьте одно фото дефекта (JPEG или PNG, до 10 МиБ).',
        DraftStep.ZONE: 'Выберите зону:',
        DraftStep.DESCRIPTION: 'Опишите замечание текстом (1–1000 символов).',
        DraftStep.ASSIGNEE: 'Выберите исполнителя:',
        DraftStep.DUE_DATE: 'Введите срок как ДД.ММ.ГГГГ (от сегодня до +365 дней).',
    }
    return texts[draft.step]


def _step_buttons(draft):
    if draft.step == DraftStep.ZONE:
        zones = list(Zone.objects.filter(active=True).order_by('name'))
        return _zone_buttons(draft, zones)
    if draft.step == DraftStep.ASSIGNEE:
        site = _draft_site(draft)
        users = list(User.objects.filter(site=site, is_active=True, role__isnull=False).order_by('display_name'))
        return _assignee_buttons(draft, users)
    if draft.step == DraftStep.CONFIRM:
        return _confirm_buttons(draft)
    return _nav_buttons(draft, back=(draft.step != DraftStep.PHOTO))
