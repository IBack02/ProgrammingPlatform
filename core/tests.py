import json

from django.test import Client, TestCase

from .models import ClassGroup, ExamAttempt, Student, Teacher
from .security import auth_version


class ExamFlowTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(
            full_name="Exam Teacher",
            pin_hash="!",
            is_active=True,
        )
        self.class_group = ClassGroup.objects.create(
            name="Exam Class",
            owner=self.teacher,
        )
        self.student = Student.objects.create(
            full_name="Exam Student",
            class_group=self.class_group,
            pin_hash="!",
            is_active=True,
        )
        self.teacher_client = Client()
        teacher_session = self.teacher_client.session
        teacher_session["teacher_id"] = self.teacher.id
        teacher_session["teacher_auth_version"] = auth_version(self.teacher.pin_hash)
        teacher_session.save()

        self.student_client = Client()
        student_session = self.student_client.session
        student_session["student_id"] = self.student.id
        student_session["student_class_id"] = self.class_group.id
        student_session["student_auth_version"] = auth_version(self.student.pin_hash)
        student_session.save()

    def _json(self, method, url, payload=None, client=None):
        client = client or self.teacher_client
        return getattr(client, method)(
            url,
            data=json.dumps(payload or {}),
            content_type="application/json",
        )

    def _create_running_exam(self):
        response = self._json(
            "post",
            "/api/teacher/exams/",
            {
                "title": "Networks",
                "duration_minutes": 30,
                "class_ids": [self.class_group.id],
            },
        )
        self.assertEqual(response.status_code, 201)
        exam_id = response.json()["exam"]["id"]

        questions = [
            {
                "position": 1,
                "question_type": "open_text",
                "prompt": "Explain TCP",
                "model_answer": "Reliable transport",
                "max_score": 5,
            },
            {
                "position": 2,
                "question_type": "matching",
                "prompt": "Match layers",
                "model_answer": "Correct pairs",
                "max_score": 4,
                "pairs": [
                    {"left_text": "HTTP", "right_text": "Application"},
                    {"left_text": "IP", "right_text": "Network"},
                ],
            },
            {
                "position": 3,
                "question_type": "diagram",
                "prompt": "Draw a topology",
                "model_answer": "Valid topology",
                "max_score": 6,
            },
        ]
        for question in questions:
            response = self._json(
                "post",
                f"/api/teacher/exams/{exam_id}/questions/",
                question,
            )
            self.assertEqual(response.status_code, 201)

        response = self._json(
            "patch",
            f"/api/teacher/exams/{exam_id}/",
            {"status": "running"},
        )
        self.assertEqual(response.status_code, 200)
        return exam_id

    def test_student_flow_hides_answers_and_locks_content(self):
        exam_id = self._create_running_exam()
        response = self._json(
            "post",
            f"/api/student/exams/{exam_id}/start/",
            client=self.student_client,
        )
        self.assertEqual(response.status_code, 200)

        response = self.student_client.get(f"/api/student/exams/{exam_id}/")
        self.assertEqual(response.status_code, 200)
        response_text = response.content.decode("utf-8")
        self.assertNotIn("model_answer", response_text)
        self.assertNotIn('"correct"', response_text)

        questions = response.json()["questions"]
        open_question = questions[0]
        response = self._json(
            "post",
            f"/api/student/exams/{exam_id}/questions/{open_question['id']}/answer/",
            {"text_answer": "TCP provides reliable transport."},
            self.student_client,
        )
        self.assertEqual(response.status_code, 200)

        response = self._json(
            "post",
            f"/api/student/exams/{exam_id}/submit/",
            {"answers": []},
            self.student_client,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["attempt"]["status"], ExamAttempt.Status.SUBMITTED)

        response = self._json(
            "post",
            f"/api/teacher/exams/{exam_id}/questions/",
            {
                "position": 4,
                "question_type": "open_text",
                "prompt": "Late question",
                "model_answer": "Must be blocked",
                "max_score": 1,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_json_import_is_atomic(self):
        response = self._json(
            "post",
            "/api/teacher/exams/import-json/",
            {
                "action": "create_exam",
                "exam": {
                    "title": "Imported exam",
                    "duration_minutes": 45,
                    "class_ids": [self.class_group.id],
                },
                "questions": [
                    {
                        "position": 1,
                        "question_type": "open_text",
                        "prompt": "Imported question",
                        "model_answer": "Imported answer",
                        "max_score": 3,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["exam"]["question_count"], 1)

        response = self._json(
            "post",
            "/api/teacher/exams/import-json/",
            {
                "action": "create_exam",
                "exam": {"title": "Broken import"},
                "questions": [
                    {
                        "position": 1,
                        "question_type": "open_text",
                        "prompt": "",
                        "model_answer": "Answer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            self.teacher.exams.filter(title="Broken import").exists()
        )
