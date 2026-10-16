import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from .exam_reports import LABELS, collect_report, compact_analysis_input, generate_analysis, render_pdf
from .models import (Teacher, ClassGroup, Student, Session, SessionTask, StudentSession,
    StudentTaskProgress, Submission, TaskTestCase, Exam, ExamQuestion, ExamAttempt, ExamAnswer,
    PeerAssessmentSession, PeerAssessmentAssignment, PeerAssessmentReview)
from .security import auth_version
from .submission_results import summarize_results


def result(token, status, output):
    return SimpleNamespace(token=token, status_id=status, stdout=output, stderr="", compile_output="", message="")


class LessonSubmissionTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(full_name="Teacher", pin_hash="!")
        auth = self.client.session
        auth.update({"teacher_id":self.teacher.id,"teacher_auth_version":auth_version("!")})
        auth.save()
        self.group = ClassGroup.objects.create(name="A", owner=self.teacher)
        self.other = ClassGroup.objects.create(name="B", owner=self.teacher)
        self.lesson = Session.objects.create(title="Lesson", author=self.teacher, status="running")
        self.lesson.allowed_classes.add(self.group, self.other)
        self.task = SessionTask.objects.create(session=self.lesson, position=1, title="Code", statement="Print")
        self.students, self.submissions = [], []
        for group in (self.group, self.other):
            student = Student.objects.create(full_name=group.name, class_group=group, pin_hash="!")
            ss = StudentSession.objects.create(student=student, session=self.lesson)
            progress = StudentTaskProgress.objects.create(student_session=ss, task=self.task)
            self.submissions.append(Submission.objects.create(progress=progress, attempt_no=1, code="print(2)",
                verdict="wrong_answer", stdout="2", passed_tests=1, total_tests=3))
            self.students.append(student)
        self.url = f"/api/teacher/tasks/{self.task.id}/submissions/"

    def test_list_and_detail_are_scoped_to_task_class_and_owner(self):
        query = f"?class_id={self.group.id}"
        rows = self.client.get(self.url + query).json()["submissions"]
        self.assertEqual([r["id"] for r in rows], [self.submissions[0].id])
        self.assertNotIn("code", rows[0])
        self.assertEqual(self.client.get(self.url + f"{self.submissions[0].id}/" + query).json()["submission"]["code"], "print(2)")
        self.assertEqual(self.client.get(self.url + f"{self.submissions[1].id}/" + query).status_code, 404)
        self.assertEqual(Client().get(self.url + query).status_code, 401)
        outsider = Teacher.objects.create(full_name="Other", pin_hash="!")
        self.lesson.author = outsider
        self.lesson.save()
        self.assertEqual(self.client.get(self.url + query).status_code, 404)

    def test_result_snapshot_counts_all_tests_and_preserves_token_order(self):
        cases = [{"ordinal": i, "stdin": str(i), "expected_stdout": "yes"} for i in range(1, 4)]
        rows, passed, verdict, stdout, _ = summarize_results(cases,
            [result("c", 3, "yes"), result("b", 4, "no"), result("a", 3, "yes")], ["a", "b", "c"])
        self.assertEqual(passed, 2)
        self.assertEqual(verdict, "wrong_answer")
        self.assertEqual(stdout, "no")
        self.assertEqual([r["stdout"] for r in rows], ["yes", "no", "yes"])
        self.assertEqual(rows[1]["ordinal"], 2)
        missing = summarize_results(cases, [result("a", 3, "yes")], ["a", "b", "c"])
        self.assertEqual(missing[2], "runtime_error")
        self.assertEqual(missing[0][1]["verdict"], "unavailable")

    @patch("core.views.wait_batch")
    @patch("core.views.create_batch_submissions", return_value=["a", "b", "c"])
    def test_student_submit_persists_all_test_details_without_exposing_them(self, create, wait):
        for i in range(1, 4):
            TaskTestCase.objects.create(task=self.task, ordinal=i, stdin=str(i), expected_stdout="yes")
        # Fixture's old submission should count as the first attempt.
        progress = self.submissions[0].progress
        progress.attempts_total = 1
        progress.save()
        wait.return_value = [result("a", 3, "yes"), result("b", 4, "no"), result("c", 3, "yes")]
        client = Client()
        auth = client.session
        auth.update({"student_id":self.students[0].id,"student_class_id":self.group.id,"student_auth_version":auth_version("!")})
        auth.save()
        response = client.post(f"/api/student/task/{self.task.id}/submit", json.dumps({"code": "print('yes')"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        saved = Submission.objects.latest("id")
        self.assertEqual(saved.passed_tests, 2)
        self.assertEqual(len(saved.test_results), 3)
        self.assertEqual(saved.programming_language, "python")
        self.assertIn("print('yes')", saved.executed_code)
        self.assertNotIn("expected_stdout", response.content.decode())


class ExamReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = Teacher.objects.create(full_name="Teacher", pin_hash="!")
        auth = self.client.session
        auth.update({"teacher_id":self.teacher.id,"teacher_auth_version":auth_version("!"),"ui_lang":"ru"})
        auth.save()
        self.group = ClassGroup.objects.create(name="Класс А", owner=self.teacher)
        self.other = ClassGroup.objects.create(name="Класс Б", owner=self.teacher)
        self.exam = Exam.objects.create(owner=self.teacher, title="Циклы", topic="Python")
        self.exam.allowed_classes.add(self.group, self.other)
        self.questions = [ExamQuestion.objects.create(exam=self.exam, position=i, question_type="open_text", prompt=f"Вопрос {i}", max_score=5) for i in (1, 2)]
        self.attempts = []
        for i, group in enumerate((self.group, self.other)):
            student = Student.objects.create(full_name=f"Ученик {i}", class_group=group, pin_hash="!")
            attempt = ExamAttempt.objects.create(exam=self.exam, student=student, status="submitted", started_at=timezone.now(), expires_at=timezone.now()+timedelta(hours=1))
            self.attempts.append(attempt)
            ExamAnswer.objects.create(attempt=attempt, question=self.questions[0], awarded_score=i*4, teacher_feedback="Нужно повторить цикл while.")
        self.peer_session = PeerAssessmentSession.objects.create(owner=self.teacher, reviewer_class=self.group, title="Отзывы")
        for index, (attempt, score, moderation) in enumerate([(self.attempts[0], 0, "objective"), (self.attempts[0], 4, "pending"), (self.attempts[1], 4, "incorrect")]):
            reviewer = Student.objects.create(full_name=f"Reviewer {index}", class_group=self.group, pin_hash="!")
            assignment = PeerAssessmentAssignment.objects.create(session=self.peer_session, reviewer=reviewer, exam_attempt=attempt)
            PeerAssessmentReview.objects.create(assignment=assignment, question=self.questions[0], score=score, moderation_status=moderation,
                comment="Проверить условие цикла", teacher_comment="Комментарий модератора")
        self.url = f"/api/teacher/exams/{self.exam.id}/report/"

    def test_statistics_handle_zero_missing_and_multiple_reviewers(self):
        data = collect_report(self.exam)
        self.assertEqual(data["stats"][0]["teacher"], 2)
        self.assertEqual(data["stats"][0]["peer"], 3)  # (mean(0,4) + 4) / 2, not mean(0,4,4)
        self.assertIsNone(data["stats"][1]["teacher"])
        self.assertEqual(data["stats"][1]["teacher_n"], 0)
        self.assertEqual(collect_report(self.exam, self.group)["stats"][0]["teacher"], 0)

    def test_compact_payload_keeps_feedback_priority_and_omits_student_names(self):
        payload = compact_analysis_input(collect_report(self.exam))
        data = json.loads(payload)
        self.assertEqual([r[4] for r in data["r"]], ["objective", "pending", "incorrect"])
        self.assertIn("Комментарий модератора", data["texts"])
        self.assertIn("Нужно повторить цикл while.", data["texts"])
        self.assertEqual(data["texts"].count("Проверить условие цикла"), 1)
        self.assertNotIn("Ученик 0", payload)
        self.assertNotIn("Reviewer", payload)
        self.assertEqual(len(data["r"]), 3)

    @patch("core.exam_reports.generate_analysis", return_value="Повторить цикл while: вопрос 1.")
    def test_report_without_assessment_uses_only_teacher_scores_and_feedback(self, analyze):
        self.peer_session.delete()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertEqual(response["X-Report-Analysis"], "ready")
        payload = json.loads(analyze.call_args.args[0])
        self.assertNotIn("r", payload)
        self.assertNotIn("r", payload["schema"])
        self.assertNotIn("peer", json.dumps(payload))
        self.assertEqual(payload["avg"], [[1, 5, 2, 2], [2, 5, None, 0]])
        self.assertEqual(payload["s"][0][1], [0, None])
        self.assertIn("Нужно повторить цикл while.", payload["texts"])
        self.assertEqual(len(payload["s"][0]), 3)

    def test_peer_sections_depend_on_actual_reviews_in_the_selected_class(self):
        PeerAssessmentReview.objects.filter(assignment__exam_attempt=self.attempts[0]).delete()
        self.assertTrue(collect_report(self.exam)["has_peer_reviews"])
        self.assertTrue(collect_report(self.exam, self.other)["has_peer_reviews"])
        # An assigned assessment with no reviews must not create empty report sections.
        data = collect_report(self.exam, self.group)
        self.assertFalse(data["has_peer_reviews"])
        self.assertNotIn("r", json.loads(compact_analysis_input(data)))

    def test_teacher_only_pdf_omits_peer_charts_tables_and_explanations_in_all_languages(self):
        from reportlab.graphics.shapes import Drawing, String
        from reportlab.platypus import Paragraph, Table

        self.peer_session.delete()
        data = collect_report(self.exam)
        for language, labels in LABELS.items():
            with self.subTest(language=language), patch("reportlab.platypus.SimpleDocTemplate.build") as build:
                render_pdf(data, "Analysis", language)
                texts, tables = [], []
                for item in build.call_args.args[0]:
                    if isinstance(item, Paragraph):
                        texts.append(item.getPlainText())
                    elif isinstance(item, Drawing):
                        texts.extend(shape.text for shape in item.contents if isinstance(shape, String))
                    elif isinstance(item, Table):
                        tables.append(item._cellvalues)
                        texts.extend(cell for row in item._cellvalues for cell in row if isinstance(cell, str))
                self.assertIn(labels["teacher_sources"], texts)
                self.assertIn(labels["teacher_note"], texts)
                self.assertNotIn(labels["sources"], texts)
                self.assertNotIn(labels["note"], texts)
                self.assertNotIn(labels["peer"], texts)
                self.assertNotIn(labels["objective"], texts)
                self.assertNotIn(f'{labels["matrix"]} — {labels["peer"]}', texts)
                self.assertEqual(len(tables), 2)  # Numeric statistics and teacher score matrix.
                self.assertEqual(tables[0][0], [labels[k] for k in ("question", "maximum", "teacher", "count")])

    def test_report_download_is_on_exams_page_and_removed_from_assessment(self):
        self.assertContains(self.client.get("/teacher/exams/"), 'id="downloadExamReportBtn"')
        self.assertNotContains(self.client.get("/teacher/assessment/"), "data-exam-report")

    @patch("core.exam_reports.generate_analysis", return_value="Успехи: вопрос 1. Повторить условия цикла.")
    def test_pdf_download_is_private_and_scoped(self, analyze):
        response = self.client.post(self.url + f"?class_id={self.group.id}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertEqual(response["X-Report-Analysis"], "ready")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(len(json.loads(analyze.call_args.args[0])["s"]), 1)
        self.assertEqual(Client().post(self.url).status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.exam.owner = Teacher.objects.create(full_name="Other", pin_hash="!")
        self.exam.save()
        self.assertEqual(self.client.post(self.url).status_code, 404)

    @patch("core.exam_reports.generate_analysis", side_effect=RuntimeError("No API key"))
    def test_failed_ai_still_downloads_statistics_without_fabricated_analysis(self, analyze):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Report-Analysis"], "unavailable")
        self.assertTrue(response.content.startswith(b"%PDF-"))

    @patch("core.exam_reports._get_openai_client")
    def test_analysis_uses_configured_api_and_reuses_unchanged_data(self, client):
        client.return_value.with_options.return_value.responses.create.return_value = SimpleNamespace(output_text="Analysis", status="completed")
        payload = compact_analysis_input(collect_report(self.exam))
        self.assertEqual(generate_analysis(payload, "ru"), "Analysis")
        self.assertEqual(generate_analysis(payload, "ru"), "Analysis")
        call = client.return_value.with_options.return_value.responses.create
        self.assertEqual(call.call_count, 1)
        self.assertFalse(call.call_args.kwargs["store"])
        self.assertEqual(call.call_args.kwargs["input"], payload)
        self.assertIn("Compare teacher and peer scores", call.call_args.kwargs["instructions"])
        self.peer_session.delete()
        teacher_payload = compact_analysis_input(collect_report(self.exam))
        generate_analysis(teacher_payload, "ru")
        self.assertEqual(call.call_count, 2)
        instructions = call.call_args.kwargs["instructions"]
        self.assertNotIn("Compare teacher and peer scores", instructions)
        self.assertIn("solely on teacher scores and teacher feedback", instructions)
        self.assertIn("Do not mention peer assessment", instructions)

    def test_pdf_handles_many_columns_long_names_and_feedback(self):
        for i in range(3, 24):
            ExamQuestion.objects.create(exam=self.exam, position=i, question_type="open_text", prompt="Длинный вопрос " * 70, max_score=1)
        data = collect_report(self.exam)
        data["rows"][0]["name"] = "Длинное имя " * 25
        pdf = render_pdf(data, "Длинный анализ. " * 500, "kk")
        self.assertTrue(pdf.startswith(b"%PDF-"))
