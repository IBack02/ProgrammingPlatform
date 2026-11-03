import io
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from docx import Document
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .exam_views import _create_question
from .exam_work_docx import LABELS
from .models import (ClassGroup, Exam, ExamAnswer, ExamAttempt, PeerAssessmentAssignment,
                     PeerAssessmentReview, PeerAssessmentSession, Student, Teacher)
from .security import auth_version


class ExamWorkExportTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(full_name="Export teacher", pin_hash="!")
        self.group = ClassGroup.objects.create(name="9А", owner=self.teacher)
        self.student = Student.objects.create(full_name="Аружан Әлі / Test", class_group=self.group, pin_hash="!")
        self.exam = Exam.objects.create(owner=self.teacher, title="Экзамен по информатике", topic="Типы данных",
                                        instructions="Прочитайте задания.\nПокажите ход решения.")
        self.exam.allowed_classes.add(self.group)
        question_data = [
            {"question_type": "open_text", "prompt": "Объясните переменную <name> & её значение.",
             "model_answer": "Именованная область памяти.", "max_score": 4},
            {"question_type": "single_choice", "prompt": "Столица Казахстана", "max_score": 3,
             "choices": [{"text": "Астана", "is_correct": True}, {"text": "Мадрид", "is_correct": False}]},
            {"question_type": "matching", "prompt": "Сопоставьте тип и пример.", "model_answer": "По одному баллу за пару.",
             "max_score": 2, "pairs": [{"left_text": "int", "right_text": "42"}, {"left_text": "str", "right_text": "hello"}]},
            {"question_type": "table", "prompt": "Заполните таблицу.", "max_score": 3,
             "table_schema": {"columns": ["Язык", "Автор"], "rows": [
                 [{"mode": "given", "value": "Python"}, {"mode": "input", "answer": "Гвидо ван Россум"}],
                 [{"mode": "given", "value": "Java"}, {"mode": "input", "answer": "Джеймс Гослинг"}],
             ]}},
            {"question_type": "diagram", "prompt": "Постройте блок схему.", "model_answer": "Начало → условие → конец.", "max_score": 2},
            {"question_type": "open_text", "prompt": "Задание без ответа.", "model_answer": "Модель пропущенного ответа.", "max_score": 1},
        ]
        self.questions = [_create_question(self.exam, {"position": i, **data}) for i, data in enumerate(question_data, 1)]
        self.attempt = ExamAttempt.objects.create(exam=self.exam, student=self.student, status="submitted",
            started_at=timezone.now() - timedelta(hours=1), expires_at=timezone.now() + timedelta(hours=1),
            submitted_at=datetime(2026, 11, 3, 6, 45, tzinfo=datetime_timezone.utc), total_score=999,
            presentation_json={str(self.questions[2].pk): {
                "left": [{"key": "L1", "text": "int"}, {"key": "L2", "text": "str"}],
                "right": [{"key": "R2", "text": "hello"}, {"key": "R1", "text": "42"}],
                "correct": {"L1": "R1", "L2": "R2"}}})
        self.answers = [
            ExamAnswer.objects.create(attempt=self.attempt, question=self.questions[0], text_answer="Ответ ученика\n    print('<safe>')",
                                      awarded_score=Decimal("2.25"), teacher_feedback="Объяснение неполное."),
            ExamAnswer.objects.create(attempt=self.attempt, question=self.questions[1], choice_answer="2", text_answer="Старый текст", awarded_score=0),
            ExamAnswer.objects.create(attempt=self.attempt, question=self.questions[2], matching_answer={"L1": "R2"}, awarded_score=1),
            ExamAnswer.objects.create(attempt=self.attempt, question=self.questions[3], table_answer={"r1c2": "Ученический ответ таблицы"}),
            ExamAnswer.objects.create(attempt=self.attempt, question=self.questions[4], diagram_xml='<mxfile><diagram name="Answer"/></mxfile>', awarded_score=1),
        ]
        self.url = reverse("teacher_exam_attempt_download", args=[self.attempt.pk])
        self.client = self.teacher_client(self.teacher)

    def teacher_client(self, teacher):
        client = Client()
        session = client.session
        session.update({"teacher_id": teacher.pk, "teacher_auth_version": auth_version(teacher.pin_hash), "ui_lang": "ru"})
        session.save()
        return client

    def document(self, response):
        self.assertEqual(response.status_code, 200, response.content[:200])
        with ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        text = "\n".join(node.text or "" for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        return Document(io.BytesIO(response.content)), text

    @override_settings(TIME_ZONE="Asia/Qyzylorda")
    def test_word_contains_identity_local_submission_date_and_saved_teacher_scores_only(self):
        assessment = PeerAssessmentSession.objects.create(owner=self.teacher, reviewer_class=self.group, title="Peer session")
        assignment = PeerAssessmentAssignment.objects.create(session=assessment, reviewer=self.student, exam_attempt=self.attempt)
        PeerAssessmentReview.objects.create(assignment=assignment, question=self.questions[0], score=4,
                                            comment="PRIVATE PEER COMMENT", teacher_comment="PRIVATE MODERATION COMMENT")
        with CaptureQueriesContext(connection) as queries, patch("requests.sessions.Session.request") as network:
            response = self.client.get(self.url)
        document, text = self.document(response)
        self.assertIn("application/vnd.openxmlformats-officedocument.wordprocessingml.document", response["Content-Type"])
        self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
        self.assertIn(".docx", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        for expected in (self.student.full_name, "9А", self.exam.title, "03.11.2026 11:45 +0500", "4.25", "15", "4 / 6",
                         "Объяснение неполное.", "Оценивание не завершено."):
            self.assertIn(expected, text)
        for excluded in ("PRIVATE PEER COMMENT", "PRIVATE MODERATION COMMENT", "999", "Peer session"):
            self.assertNotIn(excluded, text)
        self.assertFalse(any("peerassessment" in q["sql"].lower() for q in queries))
        network.assert_not_called()
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.total_score, 999)
        self.assertEqual(document.paragraphs[0].style.name, "Title")

    def test_all_answer_types_and_complete_mark_scheme_are_readable(self):
        document, text = self.document(self.client.get(self.url))
        for expected in ("Объясните переменную <name> & её значение.", "print('<safe>')", "Мадрид", "Ученический ответ таблицы", "Нет ответа"):
            self.assertIn(expected, text)
        self.assertNotIn("Старый текст", text)
        self.assertEqual(document.tables[0].cell(1, 0).text, "int")
        self.assertEqual(document.tables[0].cell(1, 1).text, "hello")  # Selected wrong pair, not the expected answer.
        self.assertEqual(document.tables[0].cell(2, 1).text, "Нет ответа")
        scheme = document.tables[-1]
        self.assertEqual(len(scheme.rows), 7)
        self.assertEqual(len(scheme.columns), 3)
        self.assertIn("Именованная область памяти.", scheme.cell(1, 1).text)
        self.assertEqual(scheme.cell(2, 1).text, "Астана")
        self.assertIn("int → 42", scheme.cell(3, 1).text)
        self.assertIn("str → hello", scheme.cell(3, 1).text)
        self.assertIn("Гвидо ван Россум", scheme.cell(4, 1).text)
        self.assertIn("Джеймс Гослинг", scheme.cell(4, 1).text)
        self.assertNotIn("r1c2", scheme.cell(4, 1).text)
        self.assertEqual([row.cells[2].text for row in scheme.rows[1:]], ["4", "3", "2", "3", "2", "1"])
        self.assertTrue(scheme.rows[0]._tr.xpath("./w:trPr/w:tblHeader"))
        self.assertGreater(text.index("Марк схема экзамена"), text.index("Задание без ответа."))

    def test_zero_and_missing_grades_are_distinct_and_unsubmitted_date_is_explicit(self):
        self.attempt.status = "in_progress"
        self.attempt.submitted_at = None
        self.attempt.save()
        self.attempt.answers.update(awarded_score=None)
        _, text = self.document(self.client.get(self.url))
        self.assertIn("Работа ещё не сдана", text)
        self.assertIn("Набрано баллов по оценке учителя: \nНе оценено", text)
        self.assertIn("0 / 6", text)
        self.attempt.answers.update(awarded_score=0)
        _, text = self.document(self.client.get(self.url))
        self.assertIn("Набрано баллов по оценке учителя: \n0", text)
        self.assertIn("5 / 6", text)

    def test_download_and_button_follow_interface_language(self):
        for language, button in (("ru", "Скачать работу Word"), ("kk", "Жұмысты Word форматында жүктеу"), ("en", "Download work as Word")):
            with self.subTest(language=language):
                session = self.client.session
                session["ui_lang"] = language
                session.save()
                page = self.client.get(reverse("teacher_exam_attempt_grade_page", args=[self.attempt.pk]))
                self.assertContains(page, button)
                self.assertContains(page, self.url)
                _, text = self.document(self.client.get(self.url))
                self.assertIn(LABELS[language]["title"], text)
                self.assertIn(LABELS[language]["scheme"], text)

    def test_owner_scope_authentication_and_read_only_method(self):
        self.assertRedirects(Client().get(self.url), "/teacher/login/", fetch_redirect_response=False)
        student_client = Client()
        session = student_client.session
        session.update({"student_id": self.student.pk, "student_class_id": self.group.pk, "student_auth_version": auth_version("!")})
        session.save()
        self.assertRedirects(student_client.get(self.url), "/teacher/login/", fetch_redirect_response=False)
        other = Teacher.objects.create(full_name="Other teacher", pin_hash="!")
        self.exam.is_shared_template = True
        self.exam.save()
        self.assertEqual(self.teacher_client(other).get(self.url).status_code, 404)
        self.assertEqual(self.client.get(reverse("teacher_exam_attempt_download", args=[9999999])).status_code, 404)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.teacher.is_active = False
        self.teacher.save()
        self.assertRedirects(self.client.get(self.url), "/teacher/login/", fetch_redirect_response=False)

    def test_diagram_links_preserve_xml_only_answers_and_enforce_ownership(self):
        diagram = self.answers[4]
        url = reverse("teacher_exam_answer_diagram_download", args=[diagram.pk])
        response = self.client.get(self.url)
        document, _ = self.document(response)
        self.assertTrue(any(rel.target_ref == "http://testserver" + url for rel in document.part.rels.values()))
        response = self.client.get(url)
        self.assertEqual(response.content.decode(), diagram.diagram_xml)
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        other = Teacher.objects.create(full_name="Other teacher", pin_hash="!")
        self.assertEqual(self.teacher_client(other).get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse("teacher_exam_answer_diagram_download", args=[self.answers[0].pk])).status_code, 404)
        diagram.diagram_xml = ""
        diagram.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_unsafe_urls_xml_controls_and_long_answers_do_not_break_word(self):
        self.questions[0].image_url = "file:///private/file"
        self.questions[0].save()
        self.answers[0].text_answer = "\x00\x01Начало\n" + "Строка кода\n" * 1000 + "Конец"
        self.answers[0].save()
        self.answers[4].diagram_file_url = "https://drive.google.com/file/d/public-id/view"
        self.answers[4].save()
        document, text = self.document(self.client.get(self.url))
        self.assertIn("Начало", text)
        self.assertIn("Конец", text)
        self.assertEqual(text.count("Строка кода"), 1000)
        links = [rel.target_ref for rel in document.part.rels.values() if rel.reltype.endswith("/hyperlink")]
        self.assertNotIn("file:///private/file", links)
        self.assertIn(self.answers[4].diagram_file_url, links)

    def test_expired_attempt_uses_existing_autograding_and_preserves_teacher_override(self):
        self.exam.auto_grade = True
        self.exam.save()
        self.attempt.status = "in_progress"
        self.attempt.submitted_at = None
        self.attempt.expires_at = timezone.now() - timedelta(minutes=1)
        self.attempt.save()
        self.answers[1].awarded_score = Decimal("1.5")
        self.answers[1].save()
        _, text = self.document(self.client.get(self.url))
        self.attempt.refresh_from_db()
        self.answers[1].refresh_from_db()
        self.assertEqual(self.attempt.status, "expired")
        self.assertEqual(self.answers[1].awarded_score, Decimal("1.5"))
        self.assertIn("5.75", text)
