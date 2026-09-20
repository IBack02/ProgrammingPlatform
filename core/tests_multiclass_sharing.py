import json

from django.test import Client, TestCase

from .models import (
    ClassGroup,
    Exam,
    ExamClass,
    ExamMatchPair,
    ExamQuestion,
    Session,
    SessionClass,
    Student,
    StudentClassMembership,
    Teacher,
)
from .security import auth_version


class MultiClassStudentTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(
            full_name="Class Teacher",
            pin_hash="!",
            is_active=True,
        )
        self.primary_class = ClassGroup.objects.create(
            name="Primary",
            owner=self.teacher,
        )
        self.secondary_class = ClassGroup.objects.create(
            name="Secondary",
            owner=self.teacher,
        )
        self.student = Student.objects.create(
            full_name="Multi Class Student",
            class_group=self.primary_class,
            pin_hash="!",
            is_active=True,
        )
        self.teacher_client = self._teacher_client(self.teacher)
        self.student_client = self._student_client(self.student)

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

    def test_primary_membership_is_created_automatically(self):
        self.assertTrue(
            StudentClassMembership.objects.filter(
                student=self.student,
                class_group=self.primary_class,
            ).exists()
        )

    def test_teacher_can_add_student_to_an_owned_secondary_class(self):
        response = self._json(
            self.teacher_client,
            "patch",
            f"/api/teacher/students/{self.student.id}/classes/",
            {"class_ids": [self.primary_class.id, self.secondary_class.id]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()["student"]["class_ids"]),
            {self.primary_class.id, self.secondary_class.id},
        )
        filtered = self.teacher_client.get(
            "/api/teacher/students/",
            {"class_id": self.secondary_class.id},
        )
        self.assertEqual(
            [row["id"] for row in filtered.json()["students"]],
            [self.student.id],
        )

    def test_primary_class_cannot_be_removed(self):
        response = self._json(
            self.teacher_client,
            "patch",
            f"/api/teacher/students/{self.student.id}/classes/",
            {"class_ids": [self.secondary_class.id]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            StudentClassMembership.objects.filter(
                student=self.student,
                class_group=self.secondary_class,
            ).exists()
        )

    def test_changing_primary_class_replaces_only_the_old_primary_membership(self):
        extra_class = ClassGroup.objects.create(name="Extra", owner=self.teacher)
        StudentClassMembership.objects.create(
            student=self.student,
            class_group=extra_class,
        )

        response = self._json(
            self.teacher_client,
            "patch",
            f"/api/teacher/students/{self.student.id}/",
            {"class_id": self.secondary_class.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["student"]["class"]["id"], self.secondary_class.id)
        self.assertEqual(
            set(response.json()["student"]["class_ids"]),
            {self.secondary_class.id, extra_class.id},
        )

    def test_teacher_cannot_assign_another_teachers_class(self):
        other_teacher = Teacher.objects.create(
            full_name="Other Teacher",
            pin_hash="!",
            is_active=True,
        )
        foreign_class = ClassGroup.objects.create(
            name="Foreign",
            owner=other_teacher,
        )

        response = self._json(
            self.teacher_client,
            "patch",
            f"/api/teacher/students/{self.student.id}/classes/",
            {"class_ids": [self.primary_class.id, foreign_class.id]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            StudentClassMembership.objects.filter(
                student=self.student,
                class_group=foreign_class,
            ).exists()
        )

    def test_secondary_membership_grants_session_and_exam_access(self):
        StudentClassMembership.objects.create(
            student=self.student,
            class_group=self.secondary_class,
        )
        lesson = Session.objects.create(
            title="Secondary lesson",
            author=self.teacher,
            status=Session.Status.RUNNING,
        )
        SessionClass.objects.create(session=lesson, class_group=self.secondary_class)

        active_response = self.student_client.get("/api/student/active-session")
        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(active_response.json()["session"]["id"], lesson.id)

        exam = Exam.objects.create(
            owner=self.teacher,
            title="Secondary exam",
            status=Exam.Status.RUNNING,
        )
        ExamClass.objects.create(exam=exam, class_group=self.secondary_class)
        ExamQuestion.objects.create(
            exam=exam,
            position=1,
            question_type=ExamQuestion.QuestionType.OPEN_TEXT,
            prompt="Explain the result",
            model_answer="Expected answer",
            max_score=2,
        )

        exams_response = self.student_client.get("/api/student/exams/")
        self.assertEqual(exams_response.status_code, 200)
        self.assertIn(exam.id, [row["id"] for row in exams_response.json()["exams"]])
        start_response = self._json(
            self.student_client,
            "post",
            f"/api/student/exams/{exam.id}/start/",
        )
        self.assertEqual(start_response.status_code, 200)


class SharedExamTests(TestCase):
    def setUp(self):
        self.author = Teacher.objects.create(
            full_name="Exam Author",
            pin_hash="!",
            is_active=True,
        )
        self.copying_teacher = Teacher.objects.create(
            full_name="Exam Copier",
            pin_hash="!",
            is_active=True,
        )
        self.client = MultiClassStudentTests._teacher_client(self.copying_teacher)
        self.source = Exam.objects.create(
            owner=self.author,
            title="Shared networks exam",
            topic="Networks",
            instructions="Answer every question",
            duration_minutes=45,
            is_shared_template=True,
        )
        question = ExamQuestion.objects.create(
            exam=self.source,
            position=1,
            question_type=ExamQuestion.QuestionType.MATCHING,
            prompt="Match the protocols",
            model_answer="Use the mark scheme",
            table_schema={"meta": {"copied": True}},
            max_score=4,
        )
        ExamMatchPair.objects.create(
            question=question,
            position=1,
            left_text="HTTP",
            right_text="Application",
        )
        ExamMatchPair.objects.create(
            question=question,
            position=2,
            left_text="IP",
            right_text="Network",
        )

    def test_shared_exam_is_listed_and_cloned_independently(self):
        listing = self.client.get("/api/teacher/exams/")
        self.assertEqual(listing.status_code, 200)
        self.assertIn(
            self.source.id,
            [row["id"] for row in listing.json()["public_exams"]],
        )

        response = MultiClassStudentTests._json(
            self.client,
            "post",
            f"/api/teacher/exams/{self.source.id}/clone/",
        )
        self.assertEqual(response.status_code, 201)
        clone = Exam.objects.get(id=response.json()["exam"]["id"])
        self.assertEqual(clone.owner, self.copying_teacher)
        self.assertEqual(clone.source_exam, self.source)
        self.assertEqual(clone.status, Exam.Status.DRAFT)
        self.assertFalse(clone.is_shared_template)
        self.assertFalse(clone.class_links.exists())
        self.assertEqual(clone.questions.count(), 1)
        self.assertEqual(clone.questions.get().matching_pairs.count(), 2)

        clone.questions.update(prompt="Changed copy")
        self.assertEqual(self.source.questions.get().prompt, "Match the protocols")
