import json
from datetime import timedelta
from decimal import Decimal

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

    def test_autofill_balances_reviewer_and_work_assignment_counts(self):
        third_reviewer = Student.objects.create(
            full_name="Reviewer Three",
            class_group=self.reviewer_class,
            pin_hash="!",
            is_active=True,
        )
        now = timezone.now()
        attempts = [
            ExamAttempt.objects.create(
                exam=self.exam,
                student=student,
                status=ExamAttempt.Status.SUBMITTED,
                started_at=now - timedelta(minutes=10),
                expires_at=now,
                submitted_at=now,
            )
            for student in (self.reviewer, self.second_reviewer, third_reviewer)
        ]
        session = PeerAssessmentSession.objects.create(
            owner=self.teacher,
            title="Balanced review",
            reviewer_class=self.reviewer_class,
        )

        response = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/peer-sessions/{session.id}/autofill/",
            {"count": 2, "source_class_id": self.reviewer_class.id},
        )
        self.assertEqual(response.status_code, 200)
        assignments = list(
            PeerAssessmentAssignment.objects.filter(session=session)
        )
        self.assertEqual(len(assignments), 6)
        self.assertEqual(
            {student.id: sum(row.reviewer_id == student.id for row in assignments) for student in (self.reviewer, self.second_reviewer, third_reviewer)},
            {self.reviewer.id: 2, self.second_reviewer.id: 2, third_reviewer.id: 2},
        )
        self.assertEqual(
            {attempt.id: sum(row.exam_attempt_id == attempt.id for row in assignments) for attempt in attempts},
            {attempt.id: 2 for attempt in attempts},
        )
        self.assertFalse(
            any(row.reviewer_id == row.exam_attempt.student_id for row in assignments)
        )

    def test_exam_filter_limits_search_manual_assignment_and_autofill(self):
        other_exam = Exam.objects.create(
            owner=self.teacher,
            title="Network basics",
            duration_minutes=30,
        )
        other_question = ExamQuestion.objects.create(
            exam=other_exam,
            position=1,
            question_type=ExamQuestion.QuestionType.OPEN_TEXT,
            prompt="Explain a network",
            model_answer="Connected devices.",
            max_score=5,
        )
        other_attempt = ExamAttempt.objects.create(
            exam=other_exam,
            student=self.author,
            status=ExamAttempt.Status.SUBMITTED,
            started_at=timezone.now() - timedelta(minutes=5),
            expires_at=timezone.now() + timedelta(minutes=25),
            submitted_at=timezone.now(),
        )
        ExamAnswer.objects.create(
            attempt=other_attempt,
            question=other_question,
            text_answer="Devices exchanging data.",
        )
        session = PeerAssessmentSession.objects.create(
            owner=self.teacher,
            title="Filtered review",
            reviewer_class=self.reviewer_class,
        )
        session.allowed_exams.set([self.exam])

        search = self.teacher_client.get(
            f"/api/teacher/peer-attempts/search/?session_id={session.id}"
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(
            [row["id"] for row in search.json()["attempts"]],
            [self.attempt.id],
        )

        rejected = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/peer-sessions/{session.id}/assignments/",
            {"reviewer_id": self.reviewer.id, "attempt_id": other_attempt.id},
        )
        self.assertEqual(rejected.status_code, 400)

        autofill = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/peer-sessions/{session.id}/autofill/",
            {"count": 1},
        )
        self.assertEqual(autofill.status_code, 200)
        self.assertFalse(
            PeerAssessmentAssignment.objects.filter(session=session).exclude(
                exam_attempt__exam=self.exam
            ).exists()
        )

        listed = self.teacher_client.get("/api/teacher/peer-sessions/")
        self.assertEqual(listed.status_code, 200)
        session_row = next(row for row in listed.json()["sessions"] if row["id"] == session.id)
        self.assertEqual(session_row["allowed_exams"], [{"id": self.exam.id, "title": self.exam.title}])
        self.assertEqual({row["id"] for row in listed.json()["exams"]}, {self.exam.id, other_exam.id})

    def test_invalid_exam_filter_does_not_partially_update_session(self):
        session = PeerAssessmentSession.objects.create(
            owner=self.teacher,
            title="Original title",
            reviewer_class=self.reviewer_class,
        )
        other_teacher = Teacher.objects.create(full_name="Foreign Exam Teacher", pin_hash="!", is_active=True)
        foreign_exam = Exam.objects.create(
            owner=other_teacher,
            title="Private exam",
            duration_minutes=20,
        )

        response = self._json(
            self.teacher_client,
            "patch",
            f"/api/teacher/peer-sessions/{session.id}/",
            {"title": "Should not persist", "exam_ids": [foreign_exam.id]},
        )
        self.assertEqual(response.status_code, 400)
        session.refresh_from_db()
        self.assertEqual(session.title, "Original title")

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
        self.assertContains(student_page, "openNextPendingQuestion")

    def test_teacher_exam_grading_page_is_private_and_saves_teacher_score(self):
        exams_page = self.teacher_client.get("/teacher/exams/")
        self.assertContains(
            exams_page,
            "/teacher/exams/attempts/${a.id}/grade/",
        )
        page = self.teacher_client.get(
            f"/teacher/exams/attempts/{self.attempt.id}/grade/"
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f"const attemptId={self.attempt.id}")
        self.assertContains(page, "/api/teacher/exam-attempts/${attemptId}/")

        answer = ExamAnswer.objects.get(attempt=self.attempt, question=self.question)
        saved = self._json(
            self.teacher_client,
            "patch",
            f"/api/teacher/exam-answers/{answer.id}/grade/",
            {"awarded_score": 4, "teacher_feedback": "Accurate answer"},
        )
        self.assertEqual(saved.status_code, 200)
        answer.refresh_from_db()
        self.assertEqual(answer.awarded_score, Decimal("4.00"))
        self.assertEqual(answer.teacher_feedback, "Accurate answer")

        other_teacher = Teacher.objects.create(
            full_name="Foreign Grader",
            pin_hash="!",
            is_active=True,
        )
        hidden = self._teacher_client(other_teacher).get(
            f"/teacher/exams/attempts/{self.attempt.id}/grade/"
        )
        self.assertEqual(hidden.status_code, 404)

    def test_results_are_grouped_by_work_and_detail_is_teacher_private(self):
        answer = ExamAnswer.objects.get(attempt=self.attempt, question=self.question)
        answer.awarded_score = Decimal("4")
        answer.teacher_feedback = "Teacher feedback"
        answer.save(update_fields=["awarded_score", "teacher_feedback", "updated_at"])
        session, assignment = self._create_assignment()
        PeerAssessmentReview.objects.create(
            assignment=assignment,
            question=self.question,
            score=Decimal("3"),
            comment="Peer feedback",
        )

        session_response = self.teacher_client.get(f"/api/teacher/peer-sessions/{session.id}/")
        self.assertEqual(session_response.status_code, 200)
        works = session_response.json()["session"]["result_works"]
        self.assertEqual(len(works), 1)
        self.assertEqual(works[0]["attempt_id"], self.attempt.id)
        self.assertEqual(works[0]["review_count"], 1)

        detail = self.teacher_client.get(
            f"/teacher/assessment/{session.id}/results/{self.attempt.id}/"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Split repeated data into related tables.")
        self.assertContains(detail, "Remove harmful redundancy.")
        self.assertContains(detail, "Teacher feedback")
        self.assertContains(detail, "Peer feedback")

        other_teacher = Teacher.objects.create(full_name="Private Results Teacher", pin_hash="!", is_active=True)
        other_client = self._teacher_client(other_teacher)
        hidden = other_client.get(
            f"/teacher/assessment/{session.id}/results/{self.attempt.id}/"
        )
        self.assertEqual(hidden.status_code, 404)

    def test_exam_chart_and_private_result_details(self):
        answer = ExamAnswer.objects.get(attempt=self.attempt, question=self.question)
        answer.awarded_score = Decimal("4")
        answer.teacher_feedback = "Teacher detail"
        answer.save(update_fields=["awarded_score", "teacher_feedback", "updated_at"])
        _, assignment = self._create_assignment()
        PeerAssessmentReview.objects.create(
            assignment=assignment,
            question=self.question,
            score=Decimal("3"),
            comment="Peer detail",
            moderation_status=PeerAssessmentReview.ModerationStatus.OBJECTIVE,
            teacher_comment="Moderator detail",
        )

        author_client = self._student_client(self.author)
        dashboard = author_client.get("/api/student/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        exam_chart = dashboard.json()["exam_chart"]
        self.assertEqual(exam_chart["attempt_ids"], [self.attempt.id])
        self.assertEqual(exam_chart["teacher_percentages"], [80.0])
        self.assertEqual(exam_chart["peer_percentages"], [60.0])

        teacher_dashboard = self.teacher_client.get("/teacher/")
        self.assertEqual(teacher_dashboard.status_code, 200)
        teacher_chart = teacher_dashboard.context["exam_chart_json"]
        self.assertEqual(teacher_chart["labels"], [self.exam.title])
        self.assertEqual(teacher_chart["teacher_percentages"], [80.0])
        self.assertEqual(teacher_chart["peer_percentages"], [60.0])
        author_card = next(
            row for row in teacher_dashboard.context["student_cards"] if row["id"] == self.author.id
        )
        self.assertEqual(author_card["avg_teacher_percent"], 80.0)
        self.assertEqual(author_card["avg_peer_percent"], 60.0)

        dashboard_page = author_client.get("/student/dashboard/")
        self.assertEqual(dashboard_page.status_code, 200)
        self.assertContains(dashboard_page, 'id="examChartButton"')
        self.assertContains(dashboard_page, "/student/exam-results/${attemptId}/")

        detail = author_client.get(f"/student/exam-results/{self.attempt.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Teacher detail")
        self.assertContains(detail, "Peer detail")
        self.assertContains(detail, "Moderator detail")
        self.assertContains(detail, self.question.model_answer)

        foreign_detail = self.student_client.get(f"/student/exam-results/{self.attempt.id}/")
        self.assertEqual(foreign_detail.status_code, 404)
