import io
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from PIL import Image

from accounts.models import Role
from issues.models import Issue, IssueStatus, Site, Zone

from .conversation import SendMessage, SetCommands, deliver, handle_update
from .models import BotDraft, DraftStep

User = get_user_model()


def near_due_date_text(days=30):
    return (timezone.localdate() + timezone.timedelta(days=days)).strftime('%d.%m.%Y')


def make_jpeg_bytes():
    buf = io.BytesIO()
    Image.new('RGB', (30, 20), (10, 200, 10)).save(buf, format='JPEG')
    return buf.getvalue()


def fake_telegram_client():
    client = MagicMock()
    client.get_file.return_value = {'file_path': 'photos/file_1.jpg'}
    client.download_file.return_value = make_jpeg_bytes()
    return client


def message_update(update_id, tg_id, **message_fields):
    message = {'message_id': update_id, 'from': {'id': tg_id}, 'chat': {'id': tg_id, 'type': 'private'}, 'date': 0}
    message.update(message_fields)
    return {'update_id': update_id, 'message': message}


def callback_update(update_id, tg_id, data):
    return {
        'update_id': update_id,
        'callback_query': {
            'id': f'cb{update_id}', 'from': {'id': tg_id},
            'message': {'chat': {'id': tg_id}}, 'data': data,
        },
    }


def run(update):
    with transaction.atomic():
        return handle_update(update)


class BotConversationTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name='Объект')
        self.zone = Zone.objects.create(site=self.site, name='Зона А')
        self.engineer = User.objects.create_user(
            username='eng1', site=self.site, role=Role.ENGINEER,
            display_name='Инженер', telegram_id=555,
        )
        self.executor = User.objects.create_user(
            username='exec1', site=self.site, role=Role.EXECUTOR,
            display_name='Исполнитель', telegram_id=556,
        )

    def test_unknown_user_gets_their_id(self):
        actions = run(message_update(1, 999999, text='/start'))
        self.assertEqual(len(actions), 1)
        self.assertIn('999999', actions[0].text)

    def test_group_messages_ignored(self):
        update = message_update(2, 555, text='/start')
        update['message']['chat']['type'] = 'group'
        actions = run(update)
        self.assertEqual(actions, [])

    def test_executor_cannot_start_new_draft(self):
        actions = run(message_update(3, 556, text='/new'))
        self.assertIn('инженер', actions[0].text.lower())
        self.assertFalse(BotDraft.objects.filter(user=self.executor).exists())

    def test_start_menu_has_buttons_matching_role(self):
        engineer_actions = run(message_update(4, 555, text='/start'))
        engineer_message = next(a for a in engineer_actions if isinstance(a, SendMessage))
        buttons = [b['callback_data'] for row in engineer_message.reply_markup['inline_keyboard'] for b in row]
        self.assertIn('menu:web', buttons)
        self.assertIn('menu:new', buttons)
        self.assertIn('menu:resume', buttons)
        self.assertIn('menu:cancel', buttons)

        executor_actions = run(message_update(5, 556, text='/start'))
        executor_message = next(a for a in executor_actions if isinstance(a, SendMessage))
        exec_buttons = [b['callback_data'] for row in executor_message.reply_markup['inline_keyboard'] for b in row]
        self.assertEqual(exec_buttons, ['menu:web'])

    def test_start_sets_native_telegram_command_menu_per_role(self):
        engineer_actions = run(message_update(41, 555, text='/start'))
        engineer_cmds = next(a for a in engineer_actions if isinstance(a, SetCommands))
        self.assertEqual(engineer_cmds.chat_id, 555)
        names = {c['command'] for c in engineer_cmds.commands}
        self.assertEqual(names, {'start', 'web', 'new', 'resume', 'cancel'})

        executor_actions = run(message_update(42, 556, text='/start'))
        executor_cmds = next(a for a in executor_actions if isinstance(a, SetCommands))
        exec_names = {c['command'] for c in executor_cmds.commands}
        self.assertEqual(exec_names, {'start', 'web'})

    def test_deliver_calls_set_my_commands_with_chat_scope(self):
        client = MagicMock()
        actions = [SetCommands(555, [{'command': 'start', 'description': 'x'}])]
        deliver(client, actions)
        client.set_my_commands.assert_called_once_with(
            [{'command': 'start', 'description': 'x'}], chat_id=555,
        )

    def test_menu_button_tap_triggers_same_action_as_command(self):
        actions = run(callback_update(6, 555, 'menu:web'))
        messages = [a for a in actions if isinstance(a, SendMessage)]
        self.assertTrue(any('login?t=' in a.text for a in messages))

    def test_menu_new_button_starts_draft_like_slash_new(self):
        run(callback_update(7, 555, 'menu:new'))
        draft = BotDraft.objects.get(user=self.engineer)
        self.assertEqual(draft.step, DraftStep.PHOTO)

    @patch('bot.conversation.TelegramClient')
    def test_full_draft_flow_creates_issue(self, mock_client_cls):
        mock_client_cls.return_value = fake_telegram_client()
        uid = iter(range(10, 100))

        # /new
        actions = run(message_update(next(uid), 555, text='/new'))
        self.assertIn('фото', actions[0].text.lower())
        draft = BotDraft.objects.get(user=self.engineer)
        self.assertEqual(draft.step, DraftStep.PHOTO)

        # фото
        actions = run(message_update(next(uid), 555, photo=[{'file_id': 'abc', 'file_size': 100}]))
        draft.refresh_from_db()
        self.assertEqual(draft.step, DraftStep.ZONE)
        self.assertTrue(any(b['callback_data'].endswith(f'zone|{self.zone.id}') for row in actions[0].reply_markup['inline_keyboard'] for b in row))

        # выбор зоны
        actions = run(callback_update(next(uid), 555, f'{draft.id.hex}|zone|{self.zone.id}'))
        draft.refresh_from_db()
        self.assertEqual(draft.step, DraftStep.DESCRIPTION)

        # описание
        actions = run(message_update(next(uid), 555, text='Трещина у окна'))
        draft.refresh_from_db()
        self.assertEqual(draft.step, DraftStep.ASSIGNEE)

        # выбор исполнителя
        actions = run(callback_update(next(uid), 555, f'{draft.id.hex}|assignee|{self.executor.id}'))
        draft.refresh_from_db()
        self.assertEqual(draft.step, DraftStep.DUE_DATE)

        # срок
        actions = run(message_update(next(uid), 555, text=near_due_date_text()))
        draft.refresh_from_db()
        self.assertEqual(draft.step, DraftStep.CONFIRM)

        # подтверждение
        actions = run(callback_update(next(uid), 555, f'{draft.id.hex}|confirm|'))
        self.assertFalse(BotDraft.objects.filter(user=self.engineer).exists())
        self.assertEqual(Issue.objects.count(), 1)
        issue = Issue.objects.first()
        self.assertEqual(issue.status, IssueStatus.OPEN)
        self.assertEqual(issue.zone_id, self.zone.id)
        self.assertEqual(issue.assignee_id, self.executor.id)
        self.assertTrue(any(isinstance(a, SendMessage) and str(issue.id) in a.text for a in actions))

    @patch('bot.conversation.TelegramClient')
    def test_confirm_twice_does_not_duplicate_issue(self, mock_client_cls):
        mock_client_cls.return_value = fake_telegram_client()
        uid = iter(range(200, 300))
        run(message_update(next(uid), 555, text='/new'))
        draft = BotDraft.objects.get(user=self.engineer)
        run(message_update(next(uid), 555, photo=[{'file_id': 'abc'}]))
        draft.refresh_from_db()
        run(callback_update(next(uid), 555, f'{draft.id.hex}|zone|{self.zone.id}'))
        run(message_update(next(uid), 555, text='Описание'))
        run(callback_update(next(uid), 555, f'{draft.id.hex}|assignee|{self.executor.id}'))
        run(message_update(next(uid), 555, text=near_due_date_text()))

        # confirm вызван дважды подряд (например, повторный клик) — карточка одна.
        run(callback_update(next(uid), 555, f'{draft.id.hex}|confirm|'))
        self.assertEqual(Issue.objects.count(), 1)

    def test_cancel_removes_draft(self):
        run(message_update(1, 555, text='/new'))
        self.assertTrue(BotDraft.objects.filter(user=self.engineer).exists())
        run(message_update(2, 555, text='/cancel'))
        self.assertFalse(BotDraft.objects.filter(user=self.engineer).exists())

    def test_back_navigation_returns_to_previous_step(self):
        run(message_update(1, 555, text='/new'))
        draft = BotDraft.objects.get(user=self.engineer)
        with patch('bot.conversation.TelegramClient') as mock_cls:
            mock_cls.return_value = fake_telegram_client()
            run(message_update(2, 555, photo=[{'file_id': 'abc'}]))
        draft.refresh_from_db()
        self.assertEqual(draft.step, DraftStep.ZONE)

        run(callback_update(3, 555, f'{draft.id.hex}|back|'))
        draft.refresh_from_db()
        self.assertEqual(draft.step, DraftStep.PHOTO)
