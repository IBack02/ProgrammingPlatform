import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from django.test import TestCase
from django.urls import reverse
from docx import Document
from pydantic import ValidationError

from .lesson_plan import ActivityValue, DOCX_CONTENT_TYPE, LessonActivity, LessonPlan, LessonStage
from .lesson_plan_docx import TEMPLATE
from .models import (
    ClassGroup, GameModule, Session, SessionTask, Student, TaskTestCase, Teacher,
    TheoryMaterialBlock, TheoryMaterialModule, TheoryQuizModule, TheoryQuizQuestion,
    TournamentQuestion, TournamentStage,
)
from .security import auth_version


def example_plan():
    respect = ActivityValue(value="respect", connection="Listen to the other learner's reasoning before responding.")
    honesty = ActivityValue(value="honesty", connection="Report failed test cases along with successful ones.")
    return LessonPlan(
        lesson_title="Loops", learning_objective="Trace iteration", lesson_objective="Write a loop",
        criteria="Checks output", language_objectives="Explain a loop",
        interdisciplinary="Mathematics", ict="Use the portal", prior_knowledge="Input and output",
        differentiation="Provide a trace table", assessment="Check 3 values",
        safety_and_connections="Check device posture",
        beginning=LessonStage(
            minutes=5, resources="Whiteboard", module_positions=[1],
            activities=[LessonActivity(description="Trace a loop in pairs", value_links=[respect])],
        ),
        middle=LessonStage(
            minutes=35, resources="", module_positions=[2, 3, 4],
            activities=[LessonActivity(description="Write and test a loop", value_links=[honesty])],
        ),
        end=LessonStage(
            minutes=5, resources="", module_positions=[],
            activities=[LessonActivity(description="Discuss results in pairs", value_links=[respect]),
                        LessonActivity(description="Complete an exit ticket", value_links=[])],
        ),
    )


class LessonPlanTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(full_name="Teacher Name", pin_hash="!", is_active=True)
        starts = datetime(2026, 11, 8, 8, 0, tzinfo=timezone.utc)
        self.lesson = Session.objects.create(
            author=self.teacher, title="Loops", description="Compare iteration patterns",
            starts_at=starts, ends_at=starts + timedelta(minutes=45),
        )
        self.group = ClassGroup.objects.create(name="9A", owner=self.teacher)
        self.lesson.allowed_classes.add(self.group)
        Student.objects.create(class_group=self.group, full_name="Private Student Name", pin_hash="!")
        self.url = reverse("teacher_lesson_plan", args=[self.lesson.pk])

    def sign_in(self, teacher):
        session = self.client.session
        session.update({"teacher_id": teacher.pk, "teacher_auth_version": auth_version(teacher.pin_hash)})
        session.save()

    def test_plan_requires_owner_and_saved_modules(self):
        self.assertEqual(self.client.post(self.url).status_code, 401)
        other = Teacher.objects.create(full_name="Other", pin_hash="!", is_active=True)
        self.sign_in(other)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.sign_in(self.teacher)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url).status_code, 400)
        self.assertContains(self.client.get(reverse("teacher_sessions_page")), 'id="lessonPlanBtn"')

    @patch("core.lesson_plan._get_openai_client")
    def test_builds_word_plan_from_ordered_modules_and_preserves_template(self, client):
        self.sign_in(self.teacher)
        self.client.post(reverse("set_ui_language"), {"lang": "en"})
        task = SessionTask.objects.create(session=self.lesson, position=2, title="Write a loop", statement="Read 3 values")
        TaskTestCase.objects.create(task=task, ordinal=1, stdin="1 2 3", expected_stdout="6", is_visible=True)
        TaskTestCase.objects.create(task=task, ordinal=2, stdin="Private hidden test", expected_stdout="7")
        material = TheoryMaterialModule.objects.create(session=self.lesson, position=1, title="Iteration")
        TheoryMaterialBlock.objects.create(module=material, ordinal=1, block_type="hidden_text", content="Reference solution")
        TheoryMaterialModule.objects.create(session=self.lesson, position=5, title="Disabled material", is_active=False)
        quiz = TheoryQuizModule.objects.create(session=self.lesson, position=3, title="Check understanding")
        TheoryQuizQuestion.objects.create(module=quiz, ordinal=1, question_type="open_answer", prompt="Explain a loop")
        tournament = GameModule.objects.create(session=self.lesson, position=4, title="Tournament", rubric="tournament")
        stage = TournamentStage.objects.create(module=tournament, ordinal=1, title="Final")
        TournamentQuestion.objects.create(
            stage=stage, ordinal=1, prompt="Select the keyword", answer="for",
            question_type="single_choice", choices=["for", "if"],
        )
        plan = example_plan()
        parse = client.return_value.__enter__.return_value.with_options.return_value.responses.parse
        parse.return_value = SimpleNamespace(status="completed", output_parsed=plan)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], DOCX_CONTENT_TYPE)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        options = parse.call_args.kwargs
        self.assertIs(options["text_format"], LessonPlan)
        self.assertFalse(options["store"])
        self.assertNotIn("Teacher Name", options["input"])
        self.assertNotIn("Private Student Name", options["input"])
        self.assertNotIn("Private hidden test", options["input"])
        payload = json.loads(options["input"])
        self.assertEqual(payload["duration_minutes"], 45)
        self.assertEqual(payload["interface_language"], "en")
        self.assertIn("Write all generated text in English", options["instructions"])
        self.assertIn("Lesson objective", payload["template"])
        self.assertEqual(len(payload["nis_value_catalog"]), 8)
        self.assertEqual(payload["nis_value_catalog"]["respect"], "Respect")
        self.assertEqual([m["position"] for m in payload["lesson"]["modules"]], [1, 2, 3, 4])
        self.assertEqual(payload["lesson"]["modules"][3]["stages"][0]["questions"][0]["choices"], ["for", "if"])

        output = io.BytesIO(b"".join(response.streaming_content))
        result = Document(output)
        table = result.tables[0]
        self.assertEqual(len(table.rows), 20)
        self.assertIn("Loops", table.cell(0, 0).text)
        self.assertIn("Teacher Name", table.cell(1, 2).text)
        self.assertIn("08.11.2026", table.cell(2, 0).text)
        self.assertIn("9A", table.cell(2, 2).text)
        self.assertEqual(result.sections[0].header.paragraphs[0].text, "Short-term lesson plan")
        self.assertIn("35 min", table.cell(13, 0).text)
        self.assertEqual(table.cell(13, 6).text, "Modules 2, 3, 4")
        self.assertIn("Write and test a loop", table.cell(13, 1).text)
        self.assertIn("Honesty — Report failed test cases", table.cell(13, 1).text)
        self.assertEqual(table.cell(7, 1).text, "Respect, Honesty")
        self.assertNotIn("Honesty", table.cell(12, 1).text)
        self.assertEqual(table.cell(19, 0).text, "")
        with ZipFile(output) as generated, ZipFile(TEMPLATE) as template:
            self.assertEqual(generated.namelist(), template.namelist())
            self.assertEqual(
                [n for n in template.namelist() if template.read(n) != generated.read(n)],
                ["word/document.xml", "word/header1.xml"],
            )

        parse.return_value = SimpleNamespace(status="incomplete", output_parsed=None)
        self.assertEqual(self.client.post(self.url).status_code, 502)

    @patch("core.lesson_plan._get_openai_client")
    def test_russian_and_kazakh_follow_interface_instead_of_source_language(self, client):
        self.sign_in(self.teacher)
        SessionTask.objects.create(session=self.lesson, position=1, title="Trace a loop", statement="Trace a loop")
        parse = client.return_value.__enter__.return_value.with_options.return_value.responses.parse
        for language, language_name, title, heading, label, connection in (
            ("ru", "Russian", "Циклы", "Краткосрочный план урока", "Уважение",
             "Ученики выслушивают объяснение партнёра."),
            ("kk", "Kazakh", "Циклдер", "Қысқа мерзімді жоспар", "Құрмет",
             "Оқушылар серіктесінің түсіндіруін тыңдайды."),
        ):
            with self.subTest(language=language):
                self.client.post(reverse("set_ui_language"), {"lang": language})
                plan = example_plan()
                plan.lesson_title = title
                plan.beginning.activities[0].value_links[0].connection = connection
                plan.middle.module_positions = [1]
                parse.return_value = SimpleNamespace(status="completed", output_parsed=plan)
                response = self.client.post(self.url)
                self.assertEqual(response.status_code, 200)
                args = parse.call_args.kwargs
                self.assertIn(f"Write all generated text in {language_name}", args["instructions"])
                payload = json.loads(args["input"])
                self.assertEqual(payload["interface_language"], language)
                self.assertEqual(payload["nis_value_catalog"]["respect"], label)
                document = Document(io.BytesIO(b"".join(response.streaming_content)))
                table = document.tables[0]
                self.assertEqual(document.sections[0].header.paragraphs[0].text, heading)
                self.assertIn(title, table.cell(0, 0).text)
                self.assertIn(f"{label} — {connection}", table.cell(12, 1).text)
                self.assertIn("Модуль 1", table.cell(12, 6).text)

    def test_nis_value_schema_rejects_values_outside_catalog(self):
        with self.assertRaises(ValidationError):
            ActivityValue(value="teamwork", connection="Learners help each other.")
