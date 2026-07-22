import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from .models import (
    ClassGroup,
    Session,
    SessionClass,
    Student,
    StudentSession,
    Teacher,
    TheoryMaterialModule,
    TheoryQuizMatchPair,
    TheoryQuizModule,
    TheoryQuizQuestion,
)
from .security import auth_version


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    LOGIN_RATE_LIMIT_MAX_ATTEMPTS=3,
    LOGIN_IDENTITY_MAX_ATTEMPTS=5,
    LOGIN_IP_MAX_ATTEMPTS=10,
    ADMIN_LOGIN_ATTEMPT_LIMIT=2,
)
class SecurityRegressionTests(TestCase):
    def setUp(self):
        self.teacher = Teacher(full_name="Security Teacher", is_active=True)
        self.teacher.set_pin("123456")
        self.teacher.save()
        self.class_group = ClassGroup.objects.create(
            name="Security Class",
            owner=self.teacher,
        )
        self.student = Student(
            full_name="Security Student",
            class_group=self.class_group,
            is_active=True,
        )
        self.student.set_pin("654321")
        self.student.save()

    @staticmethod
    def _json(client, method, url, payload=None):
        return getattr(client, method)(
            url,
            data=json.dumps(payload or {}),
            content_type="application/json",
        )

    def _teacher_client(self, teacher=None):
        client = Client()
        session = client.session
        active_teacher = teacher or self.teacher
        session["teacher_id"] = active_teacher.id
        session["teacher_auth_version"] = auth_version(active_teacher.pin_hash)
        session.save()
        return client

    def _student_client(self, class_id=None):
        client = Client()
        session = client.session
        session["student_id"] = self.student.id
        session["student_class_id"] = class_id or self.class_group.id
        session["student_auth_version"] = auth_version(self.student.pin_hash)
        session.save()
        return client

    def test_login_is_persistently_rate_limited(self):
        client = Client(REMOTE_ADDR="203.0.113.8")
        for _ in range(3):
            response = self._json(
                client,
                "post",
                "/api/auth/student-login",
                {"full_name": self.student.full_name, "pin": "000000"},
            )
            self.assertEqual(response.status_code, 401)

        response = self._json(
            client,
            "post",
            "/api/auth/student-login",
            {"full_name": self.student.full_name, "pin": "000000"},
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_admin_login_is_rate_limited(self):
        client = Client(REMOTE_ADDR="203.0.113.9")
        for _ in range(2):
            response = client.post(
                "/admin/login/",
                {"username": "missing", "password": "wrong"},
            )
            self.assertEqual(response.status_code, 200)

        response = client.post(
            "/admin/login/",
            {"username": "missing", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 429)

    def test_quiz_matching_does_not_disclose_database_pair_ids(self):
        session = Session.objects.create(
            title="Matching session",
            author=self.teacher,
            status=Session.Status.RUNNING,
        )
        SessionClass.objects.create(
            session=session,
            class_group=self.class_group,
        )
        module = TheoryQuizModule.objects.create(
            session=session,
            position=1,
            title="Matching quiz",
        )
        question = TheoryQuizQuestion.objects.create(
            module=module,
            ordinal=1,
            question_type=TheoryQuizQuestion.QuestionType.MATCHING,
            prompt="Match values",
        )
        TheoryQuizMatchPair.objects.create(
            question=question,
            ordinal=1,
            left_text="A",
            right_text="1",
        )
        TheoryQuizMatchPair.objects.create(
            question=question,
            ordinal=2,
            left_text="B",
            right_text="2",
        )

        client = self._student_client()
        response = client.get(f"/api/student/theory-quiz/{module.id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()["module"]["questions"][0]
        left = payload["left_items"]
        right = payload["right_items"]

        self.assertTrue(all(len(item["id"]) == 32 for item in left + right))
        self.assertTrue(set(item["id"] for item in left).isdisjoint(
            item["id"] for item in right
        ))

        right_by_text = {item["text"]: item["id"] for item in right}
        answers = {
            str(question.id): {
                item["id"]: right_by_text["1" if item["text"] == "A" else "2"]
                for item in left
            }
        }
        submitted = self._json(
            client,
            "post",
            f"/api/student/theory-quiz/{module.id}/submit",
            {"answers": answers},
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.json()["attempt"]["score_percent"], 100.0)

    def test_language_redirect_rejects_external_host(self):
        response = Client().post(
            "/set-ui-language/",
            {"lang": "en", "next": "https://attacker.example/phish"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/teacher/")

    def test_stale_student_class_session_is_rejected(self):
        other_class = ClassGroup.objects.create(
            name="Other Security Class",
            owner=self.teacher,
        )
        response = self._student_client(other_class.id).get("/api/student/dashboard")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "not authenticated")

    def test_pin_reset_revokes_existing_student_session(self):
        student_client = self._student_client()
        response = self._json(
            self._teacher_client(),
            "post",
            f"/api/teacher/students/{self.student.id}/reset-pin/",
            {"pin": "111222"},
        )
        self.assertEqual(response.status_code, 200)

        response = student_client.get("/api/student/dashboard")
        self.assertEqual(response.status_code, 401)

    def test_inactive_teacher_session_cannot_render_portal(self):
        client = self._teacher_client()
        self.teacher.is_active = False
        self.teacher.save(update_fields=["is_active"])
        response = client.get("/teacher/")
        self.assertRedirects(
            response,
            "/teacher/login/",
            fetch_redirect_response=False,
        )

    def test_teacher_cannot_read_another_teachers_session(self):
        other_teacher = Teacher(full_name="Other Teacher", is_active=True)
        other_teacher.set_pin("112233")
        other_teacher.save()
        other_session = Session.objects.create(
            title="Private session",
            author=other_teacher,
        )
        response = self._json(
            self._teacher_client(),
            "patch",
            f"/api/teacher/sessions/{other_session.id}/",
            {"title": "Stolen"},
        )
        self.assertEqual(response.status_code, 404)

    def test_theory_media_rejects_unsafe_and_non_youtube_urls(self):
        session = Session.objects.create(title="Media", author=self.teacher)
        module = TheoryMaterialModule.objects.create(
            session=session,
            position=1,
            title="Media module",
        )
        client = self._teacher_client()

        unsafe = self._json(
            client,
            "post",
            f"/api/teacher/theory-modules/{module.id}/blocks/",
            {"ordinal": 1, "block_type": "image", "content": "javascript:alert(1)"},
        )
        self.assertEqual(unsafe.status_code, 400)

        non_youtube = self._json(
            client,
            "post",
            f"/api/teacher/theory-modules/{module.id}/blocks/",
            {
                "ordinal": 1,
                "block_type": "video",
                "content": "https://attacker.example/video",
            },
        )
        self.assertEqual(non_youtube.status_code, 400)

    def test_chart_json_does_not_allow_script_breakout(self):
        user = get_user_model().objects.create_user(
            username="security-admin",
            password="password",
            is_staff=True,
        )
        client = Client()
        client.force_login(user)
        title = "</script><script>window.pwned=1</script>"
        chart_session = Session.objects.create(title=title, author=self.teacher)
        StudentSession.objects.create(student=self.student, session=chart_session)
        response = client.get(f"/admin-stats/student/{self.student.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, title)

    def test_security_headers_are_present(self):
        response = Client().get("/healthz/")
        self.assertIn("Content-Security-Policy", response)
        self.assertIn("Permissions-Policy", response)
        self.assertEqual(response["X-Permitted-Cross-Domain-Policies"], "none")

    def test_api_csrf_failure_is_json(self):
        client = Client(enforce_csrf_checks=True)
        response = self._json(
            client,
            "post",
            "/api/auth/teacher-login",
            {"full_name": self.teacher.full_name, "pin": "123456"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertFalse(response.json()["ok"])