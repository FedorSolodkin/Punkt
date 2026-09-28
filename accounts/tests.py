from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from core.errors import RateLimited
from issues.models import Site

from .models import LoginToken, Role
from .services import consume_login_token, issue_login_token, safe_next_path

User = get_user_model()


class LoginTokenTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name='Объект')
        self.user = User.objects.create_user(
            username='eng1', site=self.site, role=Role.ENGINEER,
            display_name='Инженер', telegram_id=42, is_active=True,
        )

    def test_token_single_use(self):
        raw = issue_login_token(self.user, '/issues')
        user, next_path = consume_login_token(raw)
        self.assertEqual(user, self.user)
        self.assertEqual(next_path, '/issues')

        user2, next_path2 = consume_login_token(raw)
        self.assertIsNone(user2)

    def test_unknown_token_rejected(self):
        user, _ = consume_login_token('not-a-real-token')
        self.assertIsNone(user)

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        raw = issue_login_token(self.user, '/issues')
        user, _ = consume_login_token(raw)
        self.assertIsNone(user)

    def test_rate_limit_blocks_after_five_tokens(self):
        for _ in range(5):
            issue_login_token(self.user, '/issues')
        with self.assertRaises(RateLimited):
            issue_login_token(self.user, '/issues')

    def test_safe_next_path_rejects_open_redirect(self):
        self.assertEqual(safe_next_path('https://evil.example/phish'), '/issues')
        self.assertEqual(safe_next_path('/issues/123'), '/issues/123')


class LoginViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name='Объект')
        self.user = User.objects.create_user(
            username='eng1', site=self.site, role=Role.ENGINEER,
            display_name='Инженер', telegram_id=42, is_active=True,
        )

    def test_login_without_token_shows_instructions(self):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/web')

    def test_full_login_flow_via_post(self):
        raw = issue_login_token(self.user, '/issues')
        response = self.client.post('/session', data={'token': raw})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/issues')

        # Токен одноразовый — повтор не пускает.
        response2 = self.client.post('/session', data={'token': raw})
        self.assertEqual(response2.status_code, 400)

    def test_logout_destroys_session(self):
        self.client.force_login(self.user)
        response = self.client.post('/logout')
        self.assertEqual(response.status_code, 302)
        registry = self.client.get('/issues')
        self.assertEqual(registry.status_code, 302)
