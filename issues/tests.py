import io
import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.utils import timezone
from PIL import Image

from accounts.models import Role
from core.errors import ApiError, Conflict, Forbidden, NotFound

from . import services
from .models import Issue, IssueStatus, Photo, PhotoKind, Site, Zone

User = get_user_model()


def make_jpeg(size=(40, 30), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='JPEG')
    return buf.getvalue()


def staged_photo(**kwargs):
    from .photos import validate_and_stage
    data = make_jpeg(**kwargs)
    upload = io.BytesIO(data)
    return validate_and_stage(upload)


class ServiceTestBase(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name='Тестовый объект')
        self.zone = Zone.objects.create(site=self.site, name='Зона А')
        self.engineer = User.objects.create_user(
            username='eng1', site=self.site, role=Role.ENGINEER, display_name='Инженер Иван',
            telegram_id=1001,
        )
        self.executor = User.objects.create_user(
            username='exec1', site=self.site, role=Role.EXECUTOR, display_name='Исполнитель Пётр',
            telegram_id=1002,
        )
        self.other_executor = User.objects.create_user(
            username='exec2', site=self.site, role=Role.EXECUTOR, display_name='Исполнитель Иной',
            telegram_id=1003,
        )
        self.manager = User.objects.create_user(
            username='mgr1', site=self.site, role=Role.MANAGER, display_name='Руководитель', telegram_id=1004,
        )

    def create_issue(self, **overrides):
        params = dict(
            actor=self.engineer, zone_id=self.zone.id, description='Трещина в стене',
            assignee_id=self.executor.id, due_date=timezone.localdate() + timezone.timedelta(days=5),
            photo_staged=staged_photo(), request_id=str(uuid.uuid4()),
        )
        params.update(overrides)
        return services.create_issue(**params)


class CreateIssueTests(ServiceTestBase):
    def test_creates_open_issue_with_before_photo(self):
        issue = self.create_issue()
        self.assertEqual(issue.status, IssueStatus.OPEN)
        self.assertEqual(issue.version, 1)
        self.assertEqual(issue.photos.filter(kind=PhotoKind.BEFORE).count(), 1)
        self.assertEqual(issue.events.count(), 1)

    def test_only_engineer_can_create(self):
        with self.assertRaises(Forbidden):
            self.create_issue(actor=self.executor)

    def test_idempotent_replay_same_request_id_same_body(self):
        request_id = str(uuid.uuid4())
        issue1 = self.create_issue(request_id=request_id)
        issue2 = self.create_issue(request_id=request_id)
        self.assertEqual(issue1.id, issue2.id)
        self.assertEqual(Issue.objects.count(), 1)

    def test_same_request_id_different_body_conflicts(self):
        request_id = str(uuid.uuid4())
        self.create_issue(request_id=request_id, description='Первое описание')
        with self.assertRaises(Conflict):
            self.create_issue(request_id=request_id, description='Другое описание')

    def test_invalid_description_length_rejected(self):
        with self.assertRaises(ApiError):
            self.create_issue(description='')

    def test_due_date_out_of_range_rejected(self):
        with self.assertRaises(ApiError):
            self.create_issue(due_date=timezone.localdate() - timezone.timedelta(days=1))

    def test_inactive_zone_rejected(self):
        self.zone.active = False
        self.zone.save()
        with self.assertRaises(ApiError):
            self.create_issue()

    def test_only_one_before_photo_constraint(self):
        issue = self.create_issue()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Photo.objects.create(
                    issue=issue, kind=PhotoKind.BEFORE,
                    original_path='x1', preview_path='x1p',
                    mime='image/jpeg', size_bytes=10, sha256='a' * 64, author=self.engineer,
                )


class SubmitReviewTests(ServiceTestBase):
    def setUp(self):
        super().setUp()
        self.issue = self.create_issue()

    def submit(self, **overrides):
        params = dict(
            actor=self.executor, issue_id=self.issue.public_id, version=self.issue.version,
            photo_staged=staged_photo(), request_id=str(uuid.uuid4()),
        )
        params.update(overrides)
        return services.submit_issue(**params)

    def test_submit_moves_to_ready(self):
        issue = self.submit()
        self.assertEqual(issue.status, IssueStatus.READY)
        self.assertEqual(issue.version, 2)

    def test_only_assignee_can_submit(self):
        with self.assertRaises(NotFound):
            self.submit(actor=self.other_executor)

    def test_version_mismatch_returns_conflict_without_write(self):
        with self.assertRaises(Conflict):
            self.submit(version=999)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, IssueStatus.OPEN)
        self.assertEqual(self.issue.version, 1)

    def test_cannot_submit_twice_without_new_version(self):
        issue = self.submit()
        with self.assertRaises(Conflict):
            services.submit_issue(
                actor=self.executor, issue_id=issue.public_id, version=issue.version,
                photo_staged=staged_photo(), request_id=str(uuid.uuid4()),
            )

    def test_review_accept_closes_issue(self):
        issue = self.submit()
        closed = services.review_issue(
            actor=self.engineer, issue_id=issue.public_id, decision='accept',
            reason=None, version=issue.version, request_id=str(uuid.uuid4()),
        )
        self.assertEqual(closed.status, IssueStatus.CLOSED)
        self.assertIsNotNone(closed.closed_at)

    def test_review_return_requires_reason(self):
        issue = self.submit()
        with self.assertRaises(ApiError):
            services.review_issue(
                actor=self.engineer, issue_id=issue.public_id, decision='return',
                reason='', version=issue.version, request_id=str(uuid.uuid4()),
            )

    def test_review_return_reopens_issue(self):
        issue = self.submit()
        reopened = services.review_issue(
            actor=self.engineer, issue_id=issue.public_id, decision='return',
            reason='Не устранено', version=issue.version, request_id=str(uuid.uuid4()),
        )
        self.assertEqual(reopened.status, IssueStatus.OPEN)
        self.assertIsNone(reopened.closed_at)

    def test_only_engineer_can_review(self):
        issue = self.submit()
        with self.assertRaises(Forbidden):
            services.review_issue(
                actor=self.executor, issue_id=issue.public_id, decision='accept',
                reason=None, version=issue.version, request_id=str(uuid.uuid4()),
            )

    def test_concurrent_double_submit_only_one_succeeds(self):
        """Два изменения одной версии: одно успешно, второе 409 (ТЗ §8.1, F2)."""
        ok = services.submit_issue(
            actor=self.executor, issue_id=self.issue.public_id, version=1,
            photo_staged=staged_photo(), request_id=str(uuid.uuid4()),
        )
        self.assertEqual(ok.version, 2)
        with self.assertRaises(Conflict):
            services.submit_issue(
                actor=self.executor, issue_id=self.issue.public_id, version=1,
                photo_staged=staged_photo(), request_id=str(uuid.uuid4()),
            )


class EditIssueTests(ServiceTestBase):
    def setUp(self):
        super().setUp()
        self.issue = self.create_issue()

    def test_engineer_can_reassign_with_reason(self):
        issue = services.edit_issue(
            actor=self.engineer, issue_id=self.issue.public_id, assignee_id=self.other_executor.id,
            due_date=timezone.localdate() + timezone.timedelta(days=10), reason='Занят на объекте',
            version=self.issue.version, request_id=str(uuid.uuid4()),
        )
        self.assertEqual(issue.assignee_id, self.other_executor.id)
        self.assertEqual(issue.version, 2)

    def test_edit_requires_reason(self):
        with self.assertRaises(ApiError):
            services.edit_issue(
                actor=self.engineer, issue_id=self.issue.public_id, assignee_id=self.other_executor.id,
                due_date=timezone.localdate() + timezone.timedelta(days=10), reason='',
                version=self.issue.version, request_id=str(uuid.uuid4()),
            )

    def test_executor_cannot_edit(self):
        with self.assertRaises(Forbidden):
            services.edit_issue(
                actor=self.executor, issue_id=self.issue.public_id, assignee_id=self.other_executor.id,
                due_date=timezone.localdate() + timezone.timedelta(days=10), reason='Причина',
                version=self.issue.version, request_id=str(uuid.uuid4()),
            )

    def test_cannot_edit_closed_issue(self):
        issue = services.submit_issue(
            actor=self.executor, issue_id=self.issue.public_id, version=self.issue.version,
            photo_staged=staged_photo(), request_id=str(uuid.uuid4()),
        )
        issue = services.review_issue(
            actor=self.engineer, issue_id=issue.public_id, decision='accept', reason=None,
            version=issue.version, request_id=str(uuid.uuid4()),
        )
        with self.assertRaises(Conflict):
            services.edit_issue(
                actor=self.engineer, issue_id=issue.public_id, assignee_id=self.other_executor.id,
                due_date=timezone.localdate() + timezone.timedelta(days=10), reason='Причина',
                version=issue.version, request_id=str(uuid.uuid4()),
            )


class VisibilityTests(ServiceTestBase):
    def setUp(self):
        super().setUp()
        self.own_issue = self.create_issue(assignee_id=self.executor.id)
        self.foreign_issue = self.create_issue(assignee_id=self.other_executor.id, request_id=str(uuid.uuid4()))

    def test_executor_sees_only_own_issues(self):
        visible_ids = set(services.visible_issues_queryset(self.executor).values_list('id', flat=True))
        self.assertEqual(visible_ids, {self.own_issue.id})

    def test_engineer_and_manager_see_all(self):
        for actor in (self.engineer, self.manager):
            visible_ids = set(services.visible_issues_queryset(actor).values_list('id', flat=True))
            self.assertEqual(visible_ids, {self.own_issue.id, self.foreign_issue.id})

    def test_executor_gets_not_found_for_foreign_issue(self):
        with self.assertRaises(NotFound):
            services.get_issue_for_actor(self.executor, self.foreign_issue.public_id)


class RegistryViewTests(ServiceTestBase):
    def setUp(self):
        super().setUp()
        self.issue = self.create_issue()

    def _login(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_registry_requires_login(self):
        response = Client().get('/issues')
        self.assertEqual(response.status_code, 302)

    def test_registry_ok_for_engineer(self):
        response = self._login(self.engineer).get('/issues')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{self.issue.id}')

    def test_registry_unknown_status_is_400(self):
        response = self._login(self.engineer).get('/issues?status=WRONG')
        self.assertEqual(response.status_code, 400)

    def test_registry_due_from_after_due_to_is_400(self):
        response = self._login(self.engineer).get('/issues?due_from=2030-01-01&due_to=2020-01-01')
        self.assertEqual(response.status_code, 400)

    def test_executor_registry_hides_foreign_issue(self):
        foreign = self.create_issue(assignee_id=self.other_executor.id, request_id=str(uuid.uuid4()))
        response = self._login(self.executor).get('/issues')
        self.assertContains(response, f'#{self.issue.id}')
        self.assertNotContains(response, f'#{foreign.id}')

    def test_photo_endpoint_hides_foreign_photo(self):
        foreign = self.create_issue(assignee_id=self.other_executor.id, request_id=str(uuid.uuid4()))
        photo = foreign.photos.first()
        response = self._login(self.executor).get(f'/photos/{photo.id}')
        self.assertEqual(response.status_code, 404)


class ApiEndpointTests(ServiceTestBase):
    def setUp(self):
        super().setUp()
        self.issue = self.create_issue()

    def _login(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_submit_requires_request_id(self):
        client = self._login(self.executor)
        photo_bytes = make_jpeg()
        response = client.post(
            f'/issues/{self.issue.public_id}/submit',
            data={'version': 1, 'photo': io.BytesIO(photo_bytes)},
            format='multipart',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'VALIDATION_ERROR')

    def test_submit_via_http_moves_to_ready(self):
        client = self._login(self.executor)
        photo_bytes = make_jpeg()
        response = client.post(
            f'/issues/{self.issue.public_id}/submit',
            data={'version': 1, 'request_id': str(uuid.uuid4()), 'photo': io.BytesIO(photo_bytes)},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['status'], 'READY')

    def test_edit_wrong_role_is_403(self):
        client = self._login(self.executor)
        response = client.post(
            f'/issues/{self.issue.public_id}/edit',
            data={
                'assignee_id': self.other_executor.id, 'due_date': str(timezone.localdate()),
                'reason': 'x', 'version': 1, 'request_id': str(uuid.uuid4()),
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
