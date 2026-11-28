"""Simultaneous tabs/devices, checker outages and lock-free external calls."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .exam_views import _create_question
from .models import (
    ClassGroup, Exam, ExamAnswer, ExamAttempt, ExamClass, ExamIntegrityEvent, Session,
    SessionClass, SessionTask, Student, StudentSession, StudentTaskProgress, Submission,
    TaskTestCase, Teacher,
)
from .security import auth_version


def _client_for(student):
    client = Client()
    session = client.session
    session.update({
        "student_id": student.id, "student_class_id": student.class_group_id,
        "student_name": student.full_name, "student_auth_version": auth_version(student.pin_hash),
    })
    session.save()
    return client


def _post(client, url, data):
    return client.post(url, json.dumps(data), content_type="application/json")


class ExamConcurrencyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = Teacher.objects.create(full_name="Teacher", pin_hash="!")
        self.group = ClassGroup.objects.create(name="10B", owner=self.teacher)
        self.student = Student.objects.create(full_name="Student", class_group=self.group, pin_hash="!")
        self.exam = Exam.objects.create(owner=self.teacher, title="Networks", status="running")
        ExamClass.objects.create(exam=self.exam, class_group=self.group)
        self.text = _create_question(self.exam, {"position": 1, "question_type": "open_text", "prompt": "Explain", "model_answer": "x", "max_score": 2})
        self.diagram = _create_question(self.exam, {"position": 2, "question_type": "diagram", "prompt": "Draw", "model_answer": "x", "max_score": 2})
        self.base = f"/api/student/exams/{self.exam.id}"
        self.laptop = _client_for(self.student)
        self.phone = _client_for(self.student)
        self.assertEqual(_post(self.laptop, self.base + "/start/", {}).status_code, 200)

    def answer(self, client, payload, question=None):
        return _post(client, f"{self.base}/questions/{(question or self.text).id}/answer/", payload)

    def test_revision_increments_and_stale_tab_cannot_overwrite_newer_answer(self):
        first = self.answer(self.laptop, {"text_answer": "draft", "base_revision": 0, "client_id": "laptop-tab-1"})
        self.assertEqual(first.json()["answer"]["revision"], 1)
        newer = self.answer(self.laptop, {"text_answer": "final answer", "base_revision": 1, "client_id": "laptop-tab-1"})
        self.assertEqual(newer.json()["answer"]["revision"], 2)

        stale = self.answer(self.phone, {"text_answer": "old text", "base_revision": 1, "client_id": "phone-tab-1"})
        self.assertEqual(stale.status_code, 409)
        body = stale.json()
        self.assertTrue(body["conflict"])
        self.assertEqual(body["answer"]["text_answer"], "final answer")
        self.assertEqual(ExamAnswer.objects.get(question=self.text).text_answer, "final answer")

    def test_clients_without_revision_keep_last_write_wins(self):
        self.answer(self.laptop, {"text_answer": "one"})
        self.assertEqual(self.answer(self.phone, {"text_answer": "two"}).status_code, 200)
        self.assertEqual(ExamAnswer.objects.get(question=self.text).text_answer, "two")

    def test_second_device_is_flagged_once_as_integrity_event(self):
        self.assertFalse(self.answer(self.laptop, {"text_answer": "a", "client_id": "laptop-tab-1"}).json()["concurrent"])
        response = self.answer(self.phone, {"text_answer": "b", "client_id": "phone-tab-1"})
        self.assertTrue(response.json()["concurrent"])
        self.answer(self.laptop, {"text_answer": "c", "client_id": "laptop-tab-1"})
        events = ExamIntegrityEvent.objects.filter(event_type="concurrent_session")
        self.assertEqual(events.count(), 1)
        teacher = Client()
        session = teacher.session
        session.update({"teacher_id": self.teacher.id, "teacher_auth_version": auth_version("!")})
        session.save()
        attempt = ExamAttempt.objects.get()
        detail = teacher.get(f"/api/teacher/exam-attempts/{attempt.id}/").json()
        self.assertIn("concurrent_session", [row["event_type"] for row in detail["integrity_events"]])

    def test_same_tab_after_pause_and_reload_is_not_flagged(self):
        self.answer(self.laptop, {"text_answer": "a", "client_id": "laptop-tab-1"})
        ExamAttempt.objects.update(active_client_seen_at=timezone.now() - timedelta(minutes=5))
        self.assertFalse(self.answer(self.phone, {"text_answer": "b", "client_id": "phone-tab-1"}).json()["concurrent"])
        self.assertFalse(ExamIntegrityEvent.objects.exists())

    def test_submit_keeps_newer_answers_from_another_tab(self):
        self.answer(self.laptop, {"text_answer": "v1", "base_revision": 0, "client_id": "laptop-tab-1"})
        self.answer(self.phone, {"text_answer": "v2 from phone", "base_revision": 1, "client_id": "phone-tab-1"})
        response = _post(self.laptop, self.base + "/submit/", {"client_id": "laptop-tab-1", "answers": [
            {"question_id": self.text.id, "text_answer": "v1", "base_revision": 1},
        ]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["skipped_stale"], 1)
        self.assertEqual(ExamAnswer.objects.get(question=self.text).text_answer, "v2 from phone")
        self.assertEqual(ExamAttempt.objects.get().status, "submitted")

    def test_google_drive_upload_runs_after_the_attempt_lock_is_released(self):
        seen = {}
        baseline_depth = len(connection.savepoint_ids)

        def fake_upload(xml, filename, existing_url):
            seen["depth"] = len(connection.savepoint_ids)
            return "https://drive.google.com/file/d/abc/view"

        with patch("core.exam_views.upload_exam_diagram", side_effect=fake_upload):
            response = self.answer(self.laptop, {"diagram_xml": "<mxfile/>", "upload_to_drive": True}, self.diagram)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"]["diagram_file_url"], "https://drive.google.com/file/d/abc/view")
        self.assertEqual(ExamAnswer.objects.get(question=self.diagram).diagram_file_url, "https://drive.google.com/file/d/abc/view")
        # TestCase wraps the test in a transaction; the view's own atomic block
        # (holding the attempt row lock) must already be closed during upload.
        self.assertEqual(seen["depth"], baseline_depth)

    def test_autosave_does_not_call_google_drive(self):
        with patch("core.exam_views.upload_exam_diagram") as upload:
            self.answer(self.laptop, {"diagram_xml": "<mxfile/>", "upload_to_drive": False}, self.diagram)
        upload.assert_not_called()


class JudgeOutageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = Teacher.objects.create(full_name="Teacher", pin_hash="!")
        self.group = ClassGroup.objects.create(name="8A", owner=self.teacher)
        self.student = Student.objects.create(full_name="Coder", class_group=self.group, pin_hash="!")
        self.lesson = Session.objects.create(title="Loops", status="running", author=self.teacher)
        SessionClass.objects.create(session=self.lesson, class_group=self.group)
        self.task = SessionTask.objects.create(session=self.lesson, position=1, title="Sum", statement="a+b")
        TaskTestCase.objects.create(task=self.task, ordinal=1, stdin="1 2", expected_stdout="3")
        self.client = _client_for(self.student)
        self.url = f"/api/student/task/{self.task.id}/submit"

    @patch("core.views.wait_batch", side_effect=TimeoutError("judge0 slow"))
    @patch("core.views.create_batch_submissions", return_value=["t1"])
    def test_checker_failure_is_not_counted_and_same_code_can_be_resent(self, create, wait):
        response = _post(self.client, self.url, {"code": "print(3)"})
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["retryable"])
        progress = StudentTaskProgress.objects.get()
        self.assertEqual((progress.attempts_total, progress.attempts_failed, progress.last_code_hash), (0, 0, ""))
        self.assertFalse(Submission.objects.exists())

        wait.side_effect = None
        wait.return_value = [SimpleNamespace(token="t1", status_id=3, stdout="3", stderr="", compile_output="", message="")]
        again = _post(self.client, self.url, {"code": "print(3)"})
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json()["submission"]["verdict"], "accepted")
        self.assertEqual(StudentTaskProgress.objects.get().attempts_total, 1)

    @patch("core.views.wait_batch", return_value=[SimpleNamespace(token="t1", status_id=1, stdout="", stderr="", compile_output="", message="")])
    @patch("core.views.create_batch_submissions", return_value=["t1"])
    def test_queue_still_pending_after_budget_is_not_a_failed_attempt(self, create, wait):
        self.assertEqual(_post(self.client, self.url, {"code": "print(3)"}).status_code, 503)
        self.assertEqual(StudentTaskProgress.objects.get().attempts_failed, 0)
        kwargs = wait.call_args.kwargs
        self.assertIn("deadline", kwargs)

    @patch("core.views.create_batch_submissions", return_value=["t1"])
    def test_wait_is_bounded_by_the_configured_budget(self, create):
        from core import judge0_client

        pending = [judge0_client.Judge0Item("t1", 2, "", "", "", "", "")]
        with patch.object(judge0_client, "get_batch_results", return_value=pending) as poll, \
                patch.object(judge0_client.time, "sleep"):
            items = judge0_client.wait_batch(["t1"], poll_interval=0.9, deadline=judge0_client.time.monotonic() + 0.05)
        self.assertEqual(items[0].status_id, 2)
        self.assertLessEqual(poll.call_args.kwargs["timeout"], 10.0)


class StudentSessionListTests(TestCase):
    def setUp(self):
        self.group = ClassGroup.objects.create(name="7A")
        self.student = Student.objects.create(full_name="Pupil", class_group=self.group, pin_hash="!")
        now = timezone.now()
        self.closed = []
        for index in range(12):
            session = Session.objects.create(title=f"Old {index}", status="closed", starts_at=now - timedelta(days=index + 2))
            SessionClass.objects.create(session=session, class_group=self.group)
            StudentSession.objects.create(student=self.student, session=session, started_at=now)
            self.closed.append(session)
        self.running = Session.objects.create(title="Today", status="running", starts_at=None)
        SessionClass.objects.create(session=self.running, class_group=self.group)
        self.client = _client_for(self.student)

    def test_listing_uses_constant_number_of_queries(self):
        from core.views import _student_accessible_sessions

        with CaptureQueriesContext(connection) as queries:
            sessions = _student_accessible_sessions(class_id=self.group.id, student_id=self.student.id)
        self.assertEqual(len(sessions), 13)
        self.assertLessEqual(len(queries), 4)

    def test_default_session_is_the_one_running_now(self):
        data = self.client.get("/api/student/active-session").json()
        self.assertEqual(data["session"]["id"], self.running.id)
