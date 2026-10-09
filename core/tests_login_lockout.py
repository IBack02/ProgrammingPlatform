import json
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import ClassGroup, SecurityThrottle, Student, Teacher


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    LOGIN_RATE_LIMIT_MAX_ATTEMPTS=5,
    LOGIN_IDENTITY_MAX_ATTEMPTS=5,
    LOGIN_IP_MAX_ATTEMPTS=40,
    LOGIN_RATE_LIMIT_WINDOW_SECONDS=900,
    LOGIN_RATE_LIMIT_LOCK_SECONDS=900,
)
class LoginLockoutTests(TestCase):
    def setUp(self):
        self.teacher = Teacher(full_name="Lockout Teacher", is_active=True)
        self.teacher.set_password("Secret123")
        self.teacher.save()
        group = ClassGroup.objects.create(name="Lockout class", owner=self.teacher)
        self.student = Student(full_name="Lockout Student", class_group=group, is_active=True)
        self.student.set_pin("123456")
        self.student.save()

    def login(self, role, *, client=None, correct=False, page=False, name=None):
        client = client or Client()
        identity = name or getattr(self, role).full_name
        payload = {"full_name": identity}
        payload["pin" if role == "student" else "password"] = (
            ("123456" if role == "student" else "Secret123") if correct else "000000"
        )
        if page:
            return client.post(f"/{role}/login/", payload)
        return client.post(f"/api/auth/{role}-login", json.dumps(payload), content_type="application/json")

    def test_fifth_failure_blocks_both_roles_across_ips_and_api_and_form(self):
        for role in ("student", "teacher"):
            with self.subTest(role=role):
                for attempt in range(5):
                    response = self.login(role, client=Client(REMOTE_ADDR=f"203.0.113.{attempt+1}"), page=attempt % 2 == 1)
                    self.assertEqual(response.status_code, 429 if attempt == 4 else (200 if attempt % 2 else 401))
                self.assertEqual(response.json()["retry_after_seconds"], 900)
                locked = self.login(role, correct=True, name=f"  {getattr(self, role).full_name.upper()}  ", client=Client(REMOTE_ADDR="198.51.100.1"))
                self.assertEqual(locked.status_code, 429)
                self.assertNotIn(f"{role}_id", locked.wsgi_request.session)
                form = self.login(role, correct=True, page=True)
                self.assertEqual(form.status_code, 429)
                self.assertContains(form, 'id="loginError"', status_code=429)
                self.assertEqual(form["Retry-After"], "900")

    def test_blocked_attempts_do_not_extend_deadline_and_expiry_resets_counter(self):
        now = timezone.now()
        with patch("core.security.timezone.now", return_value=now) as clock:
            for _ in range(5):
                self.login("student")
            clock.return_value = now + timedelta(seconds=300)
            blocked = self.login("student", correct=True)
            self.assertEqual(blocked["Retry-After"], "600")
            self.assertEqual(blocked.json()["retry_after_seconds"], 600)
            clock.return_value = now + timedelta(seconds=899, milliseconds=999)
            self.assertEqual(self.login("student")["Retry-After"], "1")
            clock.return_value = now + timedelta(seconds=900)
            self.assertEqual(self.login("student").status_code, 401)
            self.assertEqual(self.login("student", correct=True).status_code, 200)

    def test_success_clears_previous_failures_and_other_account_is_not_locked(self):
        for _ in range(4):
            self.assertEqual(self.login("teacher").status_code, 401)
        self.assertEqual(self.login("teacher", correct=True).status_code, 200)
        for _ in range(4):
            self.assertEqual(self.login("teacher").status_code, 401)
        self.assertEqual(self.login("teacher").status_code, 429)
        self.assertEqual(self.login("student", correct=True).status_code, 200)

    def test_nonexistent_identity_receives_same_lockout_and_no_plaintext_name_is_stored(self):
        name = "Missing Account"
        for _ in range(4):
            self.assertEqual(self.login("teacher", name=name).status_code, 401)
        self.assertEqual(self.login("teacher", name=name).status_code, 429)
        self.assertTrue(SecurityThrottle.objects.exists())
        self.assertFalse(SecurityThrottle.objects.filter(key_hash__icontains="missing").exists())

    @override_settings(LOGIN_RATE_LIMIT_WINDOW_SECONDS=3600, LOGIN_RATE_LIMIT_LOCK_SECONDS=60)
    def test_expired_short_lock_does_not_immediately_relock_within_old_window(self):
        now = timezone.now()
        with patch("core.security.timezone.now", return_value=now) as clock:
            for _ in range(5):
                self.login("teacher")
            clock.return_value = now + timedelta(seconds=60)
            for _ in range(4):
                self.assertEqual(self.login("teacher").status_code, 401)
            self.assertEqual(self.login("teacher").status_code, 429)
