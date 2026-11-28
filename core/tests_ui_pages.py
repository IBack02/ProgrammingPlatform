"""Rendered pages: inline scripts parse, and autosave pages have no Save/Load buttons."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from .exam_views import _create_question
from .models import (
    ClassGroup, Exam, ExamAttempt, ExamClass, PeerAssessmentAssignment, PeerAssessmentSession,
    Session, SessionClass, Student, Teacher,
)
from .security import auth_version

NODE = shutil.which("node")
SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", re.S)


class AutosavePagesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = Teacher.objects.create(full_name="Teacher", pin_hash="!")
        self.group = ClassGroup.objects.create(name="9A", owner=self.teacher)
        self.student = Student.objects.create(full_name="Student", class_group=self.group, pin_hash="!")
        self.author = Student.objects.create(full_name="Author", class_group=self.group, pin_hash="!")
        lesson = Session.objects.create(title="Lesson", status="running", author=self.teacher)
        SessionClass.objects.create(session=lesson, class_group=self.group)
        self.exam = Exam.objects.create(owner=self.teacher, title="Exam", status="running")
        ExamClass.objects.create(exam=self.exam, class_group=self.group)
        _create_question(self.exam, {"position": 1, "question_type": "open_text", "prompt": "Q", "model_answer": "A", "max_score": 1})
        now = timezone.now()
        self.attempt = ExamAttempt.objects.create(exam=self.exam, student=self.author, started_at=now, expires_at=now, status="submitted")
        self.peer = PeerAssessmentSession.objects.create(owner=self.teacher, title="Peer", reviewer_class=self.group, status="running")
        PeerAssessmentAssignment.objects.create(session=self.peer, reviewer=self.student, exam_attempt=self.attempt)
        self.teacher_client = Client()
        session = self.teacher_client.session
        session.update({"teacher_id": self.teacher.id, "teacher_auth_version": auth_version("!")})
        session.save()
        self.student_client = Client()
        session = self.student_client.session
        session.update({"student_id": self.student.id, "student_class_id": self.group.id,
                        "student_name": self.student.full_name, "student_auth_version": auth_version("!")})
        session.save()

    def pages(self):
        return {
            "teacher modules": self.teacher_client.get("/teacher/modules/"),
            "teacher exams": self.teacher_client.get("/teacher/exams/"),
            "teacher peer": self.teacher_client.get("/teacher/assessment/"),
            "teacher grading": self.teacher_client.get(f"/teacher/exams/attempts/{self.attempt.id}/grade/"),
            "teacher peer result": self.teacher_client.get(f"/teacher/assessment/{self.peer.id}/results/{self.attempt.id}/"),
            "student exams": self.student_client.get("/student/exams/"),
            "student peer": self.student_client.get("/student/peer-assessment/"),
            "student portal": self.student_client.get("/student/"),
        }

    def test_pages_render_without_manual_save_or_load_buttons(self):
        removed = ['id="loadBtn"', 'id="saveModuleBtn"', 'id="saveExamBtn"', 'id="refreshAttemptsBtn"',
                   'id="saveSession"', 'id="saveModal"', 'id="refreshSessions"', 'id="saveReview"', 'class="save-grade"']
        for name, response in self.pages().items():
            with self.subTest(page=name):
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn("core/error_reporter", html)
                for marker in removed:
                    self.assertNotIn(marker, html)

    @skipUnless(NODE, "node is required to syntax-check inline scripts")
    def test_inline_scripts_parse(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, response in self.pages().items():
                for index, (attrs, code) in enumerate(SCRIPT_RE.findall(response.content.decode())):
                    if not code.strip() or "json" in attrs:
                        continue
                    path = Path(folder) / f"{name.replace(' ', '_')}_{index}.js"
                    path.write_text(code, encoding="utf-8")
                    result = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True)
                    with self.subTest(page=name, script=index):
                        self.assertEqual(result.returncode, 0, result.stderr[-800:])
