import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from .models import (
    ClassGroup,
    Exam,
    ExamAnswer,
    ExamAttempt,
    ExamQuestion,
    PeerAssessmentAssignment,
    PeerAssessmentReview,
    PeerAssessmentSession,
    Student,
    Teacher,
)
from .security import auth_version


class PeerAssessmentFlowTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(full_name="Peer Teacher", pin_hash="!", is_active=True)
        self.reviewer_class = ClassGroup.objects.create(name="Reviewers", owner=self.teacher)
        self.source_class = ClassGroup.objects.create(name="Authors", owner=self.teacher)
        self.reviewer = Student.objects.create(
            full_name="Reviewer One",
            class_group=self.reviewer_class,
            pin_hash="!",
            is_active=True,
        )
        self.second_reviewer = Student.objects.create(
            full_name="Reviewer Two",
            class_group=self.reviewer_class,
            pin_hash="!",
            is_active=True,
        )
        self.author = Student.objects.create(
            full_name="Answer Author",
            class_group=self.source_class,
            pin_hash="!",
            is_active=True,
        )
        self.exam = Exam.objects.create(owner=self.teacher, title="Data models", duration_minutes=30)
        self.question = ExamQuestion.objects.create(
            exam=self.exam,
            position=1,
            question_type=ExamQuestion.QuestionType.OPEN_TEXT,
            prompt="Explain normalization",
            model_answer="Remove harmful redundancy.",
            max_score=5,
        )
        now = timezone.now()
        self.attempt = ExamAttempt.objects.create(
            exam=self.exam,
            student=self.author,
            status=ExamAttempt.Status.SUBMITTED,
            started_at=now - timedelta(minutes=10),
            expires_at=now + timedelta(minutes=20),
            submitted_at=now,
        )
        ExamAnswer.objects.create(
            attempt=self.attempt,
            question=self.question,
            text_answer="Split repeated data into related tables.",
        )
        self.teacher_client = self._teacher_client(self.teacher)
        self.student_client = self._student_client(self.reviewer)

    @staticmethod
    def _teacher_client(teacher):
        client = Client()
        session = client.session
        session["teacher_id"] = teacher.id
        session["teacher_auth_version"] = auth_version(teacher.pin_hash)
        session.save()
        return client

    @staticmethod
    def _student_client(student):
        client = Client()
        session = client.session
        session["student_id"] = student.id
        session["student_class_id"] = student.class_group_id
        session["student_auth_version"] = auth_version(student.pin_hash)
        session.save()
        return client

    @staticmethod
    def _json(client, method, url, payload=None):
        return getattr(client, method)(
            url,
            data=json.dumps(payload or {}),
            content_type="application/json",
        )

    def _create_assignment(self):
        session = PeerAssessmentSession.objects.create(
            owner=self.teacher,
            title="Normalization review",
            reviewer_class=self.reviewer_class,
        )
        assignment = PeerAssessmentAssignment.objects.create(
            session=session,
            reviewer=self.reviewer,
            exam_attempt=self.attempt,
        )
        session.status = PeerAssessmentSession.Status.RUNNING
        session.save(update_fields=["status", "updated_at"])
        return session, assignment

    def test_full_review_and_moderation_flow(self):
        session, assignment = self._create_assignment()

        listed = self.student_client.get("/api/student/peer-sessions/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["sessions"][0]["assignments"][0]["attempt_id"], self.attempt.id)
        self.assertNotIn(self.author.full_name, listed.content.decode("utf-8"))

        detail = self.student_client.get(f"/api/student/peer-assignments/{assignment.id}/")
        self.assertEqual(detail.status_code, 200)
        question = detail.json()["assignment"]["questions"][0]
        self.assertEqual(question["mark_scheme"]["model_answer"], "Remove harmful redundancy.")
        self.assertEqual(question["answer"]["text_answer"], "Split repeated data into related tables.")

        invalid = self._json(
            self.student_client,
            "post",
            f"/api/student/peer-assignments/{assignment.id}/questions/{self.question.id}/review/",
            {"score": 6, "comment": "Too high"},
        )
        self.assertEqual(invalid.status_code, 400)

        saved = self._json(
            self.student_client,
            "post",
            f"/api/student/peer-assignments/{assignment.id}/questions/{self.question.id}/review/",
            {"score": 4, "comment": "Mostly correct"},
        )
        self.assertEqual(saved.status_code, 200)
        review = PeerAssessmentReview.objects.get(assignment=assignment, question=self.question)

        moderated = self._json(
            self.teacher_client,
            "patch",
            f"/api/teacher/peer-reviews/{review.id}/moderate/",
            {"moderation_status": "objective", "teacher_comment": "Agreed"},
        )
        self.assertEqual(moderated.status_code, 200)

        changed = self._json(
            self.student_client,
            "post",
            f"/api/student/peer-assignments/{assignment.id}/questions/{self.question.id}/review/",
            {"score": 3, "comment": "Reconsidered"},
        )
        self.assertEqual(changed.status_code, 200)
        review.refresh_from_db()
        self.assertEqual(review.moderation_status, PeerAssessmentReview.ModerationStatus.PENDING)
        self.assertEqual(review.teacher_comment, "")

    def test_manual_assignment_rejects_self_and_unowned_data(self):
        session = PeerAssessmentSession.objects.create(
            owner=self.teacher,
            title="Manual review",
            reviewer_class=self.source_class,
        )
        own_work = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/peer-sessions/{session.id}/assignments/",
            {"reviewer_id": self.author.id, "attempt_id": self.attempt.id},
        )
        self.assertEqual(own_work.status_code, 400)

        other_teacher = Teacher.objects.create(full_name="Other Teacher", pin_hash="!", is_active=True)
        other_client = self._teacher_client(other_teacher)
        hidden = other_client.get(f"/api/teacher/peer-sessions/{session.id}/")
        self.assertEqual(hidden.status_code, 404)
        search = other_client.get("/api/teacher/peer-attempts/search/?q=Authors")
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["attempts"], [])

    def test_autofill_uses_requested_source_class(self):
        session = PeerAssessmentSession.objects.create(
            owner=self.teacher,
            title="Automatic review",
            reviewer_class=self.reviewer_class,
        )
        response = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/peer-sessions/{session.id}/autofill/",
            {"count": 1, "source_class_id": self.source_class.id},
        )
        self.assertEqual(response.status_code, 200)
        assignments = PeerAssessmentAssignment.objects.filter(session=session)
        self.assertEqual(assignments.count(), 2)
        self.assertFalse(assignments.exclude(exam_attempt__student__class_group=self.source_class).exists())

    def test_student_cannot_read_another_reviewers_assignment(self):
        _, assignment = self._create_assignment()
        other_client = self._student_client(self.second_reviewer)
        response = other_client.get(f"/api/student/peer-assignments/{assignment.id}/")
        self.assertEqual(response.status_code, 404)

    def test_teacher_and_student_pages_render(self):
        teacher_page = self.teacher_client.get("/teacher/assessment/")
        self.assertEqual(teacher_page.status_code, 200)
        self.assertContains(teacher_page, "/api/teacher/peer-sessions/")

        student_page = self.student_client.get("/student/peer-assessment/")
        self.assertEqual(student_page.status_code, 200)
        self.assertContains(student_page, "/api/student/peer-sessions/")
