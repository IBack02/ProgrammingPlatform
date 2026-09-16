import json

from django.test import Client, TestCase

from .models import (
    ClassGroup,
    ExamAnswer,
    ExamIntegrityEvent,
    ExamAttempt,
    GameModule,
    MindRacePrompt,
    Session,
    SessionTask,
    Student,
    TaskCodeFragment,
    TaskTestCase,
    Teacher,
    TheoryMaterialBlock,
    TheoryMaterialModule,
    TheoryQuizModule,
    TournamentStage,
    WonderFieldQuestion,
)
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
            },            {
                "position": 4,
                "question_type": "table",
                "prompt": "Complete the protocol table",
                "max_score": 3,
                "table_schema": {
                    "columns": [{"label": "Protocol"}, {"label": "Property"}],
                    "rows": [
                        {
                            "cells": [
                                {"mode": "given", "value": "TCP"},
                                {"mode": "input", "answer": "secret-table-answer"},
                            ]
                        }
                    ],
                },
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
        attempt_id = response.json()["attempt"]["id"]
        result_page = self.student_client.get(f"/student/exam-results/{attempt_id}/")
        self.assertEqual(result_page.status_code, 200)
        self.assertContains(result_page, "Reliable transport")
        self.assertContains(result_page, "secret-table-answer")

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

    def test_table_answers_hide_expected_values_and_reject_fixed_cells(self):
        exam_id = self._create_running_exam()
        started = self._json(
            "post",
            f"/api/student/exams/{exam_id}/start/",
            client=self.student_client,
        )
        self.assertEqual(started.status_code, 200)

        detail = self.student_client.get(f"/api/student/exams/{exam_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn("secret-table-answer", detail.content.decode("utf-8"))
        table_question = next(
            question
            for question in detail.json()["questions"]
            if question["question_type"] == "table"
        )
        cells = table_question["table_schema"]["rows"][0]["cells"]
        fixed_key = cells[0]["key"]
        input_key = cells[1]["key"]

        saved = self._json(
            "post",
            f"/api/student/exams/{exam_id}/questions/{table_question['id']}/answer/",
            {"table_answer": {input_key: "Reliable transport"}},
            self.student_client,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["answer"]["table_answer"][input_key], "Reliable transport")

        rejected = self._json(
            "post",
            f"/api/student/exams/{exam_id}/questions/{table_question['id']}/answer/",
            {"table_answer": {fixed_key: "Tampered"}},
            self.student_client,
        )
        self.assertEqual(rejected.status_code, 400)
        answer = ExamAnswer.objects.get(
            attempt__exam_id=exam_id,
            question_id=table_question["id"],
        )
        self.assertNotIn(fixed_key, answer.table_answer)

    def test_json_commands_can_update_and_delete_question_by_id(self):
        created_exam = self._json(
            "post",
            "/api/teacher/exams/",
            {"title": "Command exam", "duration_minutes": 30},
        )
        self.assertEqual(created_exam.status_code, 201)
        exam_id = created_exam.json()["exam"]["id"]
        created_question = self._json(
            "post",
            f"/api/teacher/exams/{exam_id}/questions/",
            {
                "position": 1,
                "question_type": "open_text",
                "prompt": "Original question",
                "model_answer": "Original answer",
                "max_score": 2,
            },
        )
        self.assertEqual(created_question.status_code, 201)
        question_id = created_question.json()["question"]["id"]

        updated = self._json(
            "post",
            "/api/teacher/exams/import-json/",
            {
                "action": "update_question",
                "question_id": question_id,
                "question": {"prompt": "Updated by ID", "max_score": 5},
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["exam"]["questions"][0]["prompt"], "Updated by ID")
        self.assertEqual(updated.json()["exam"]["questions"][0]["max_score"], 5.0)

        deleted = self._json(
            "post",
            "/api/teacher/exams/import-json/",
            {"action": "delete_question", "question_id": question_id},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["exam"]["question_count"], 0)

    def test_table_question_allows_empty_input_cells_without_model_answer(self):
        created_exam = self._json(
            "post",
            "/api/teacher/exams/",
            {"title": "Blank table", "duration_minutes": 30},
        )
        exam_id = created_exam.json()["exam"]["id"]
        response = self._json(
            "post",
            f"/api/teacher/exams/{exam_id}/questions/",
            {
                "position": 1,
                "question_type": "table",
                "prompt": "Fill the table",
                "max_score": 3,
                "table_schema": {
                    "columns": [{"label": "Term"}, {"label": "Definition"}],
                    "rows": [
                        {
                            "cells": [
                                {"mode": "given", "value": ""},
                                {"mode": "input", "answer": ""},
                            ]
                        }
                    ],
                },
            },
        )
        self.assertEqual(response.status_code, 201)
        question = response.json()["question"]
        self.assertEqual(question["table_schema"]["rows"][0]["cells"][1]["answer"], "")
    def test_integrity_events_are_live_and_idempotent(self):
        exam_id = self._create_running_exam()
        started = self._json(
            "post",
            f"/api/student/exams/{exam_id}/start/",
            client=self.student_client,
        )
        attempt_id = started.json()["attempt"]["id"]
        payload = {
            "events": [
                {
                    "event_type": "tab_hidden",
                    "client_event_id": "eventtabhidden01",
                    "client_at": "2026-07-23T10:00:00Z",
                },
                {
                    "event_type": "shortcut",
                    "client_event_id": "eventshortcut01",
                    "detail": "ctrl+u",
                    "client_at": "2026-07-23T10:00:01Z",
                },
            ]
        }
        recorded = self._json(
            "post",
            f"/api/student/exams/{exam_id}/integrity/",
            payload,
            self.student_client,
        )
        self.assertEqual(recorded.status_code, 200)
        duplicate = self._json(
            "post",
            f"/api/student/exams/{exam_id}/integrity/",
            {"events": [payload["events"][0]]},
            self.student_client,
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(
            ExamIntegrityEvent.objects.filter(attempt_id=attempt_id).count(),
            2,
        )

        attempts = self.teacher_client.get(f"/api/teacher/exams/{exam_id}/attempts/")
        self.assertEqual(attempts.json()["attempts"][0]["integrity_event_count"], 2)
        review = self.teacher_client.get(f"/api/teacher/exam-attempts/{attempt_id}/")
        self.assertEqual(len(review.json()["integrity_events"]), 2)

    def test_student_and_teacher_can_change_pin_and_revoke_old_sessions(self):
        self.student.set_pin("654321")
        self.student.save(update_fields=["pin_hash"])
        current_session = self.student_client.session
        current_session["student_auth_version"] = auth_version(self.student.pin_hash)
        current_session.save()
        old_student_client = Client()
        old_session = old_student_client.session
        old_session["student_id"] = self.student.id
        old_session["student_class_id"] = self.class_group.id
        old_session["student_auth_version"] = auth_version(self.student.pin_hash)
        old_session.save()

        pin_page = self.student_client.get("/student/change-pin/")
        self.assertContains(pin_page, 'type="password"', count=3)
        self.assertContains(self.student_client.get("/student/login/"), 'type="password" name="pin"')
        changed = self.student_client.post(
            "/student/change-pin/",
            {
                "current_pin": "654321",
                "new_pin": "112233",
                "confirm_pin": "112233",
            },
        )
        self.assertEqual(changed.status_code, 200)
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_pin("112233"))
        self.assertEqual(self.student_client.get("/api/student/dashboard").status_code, 200)
        self.assertEqual(old_student_client.get("/api/student/dashboard").status_code, 401)

        self.teacher.set_password("teacher7")
        self.teacher.save(update_fields=["pin_hash"])
        teacher_session = self.teacher_client.session
        teacher_session["teacher_auth_version"] = auth_version(self.teacher.pin_hash)
        teacher_session.save()
        old_teacher_client = Client()
        old_teacher_session = old_teacher_client.session
        old_teacher_session["teacher_id"] = self.teacher.id
        old_teacher_session["teacher_auth_version"] = auth_version(self.teacher.pin_hash)
        old_teacher_session.save()

        pin_page = self.teacher_client.get("/teacher/change-pin/")
        self.assertContains(pin_page, 'type="password"', count=3)
        self.assertContains(self.teacher_client.get("/teacher/login/"), 'type="password" name="password"')
        changed = self.teacher_client.post(
            "/teacher/change-pin/",
            {
                "current_pin": "teacher7",
                "new_pin": "updated8",
                "confirm_pin": "updated8",
            },
        )
        self.assertEqual(changed.status_code, 200)
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.check_password("updated8"))
        self.assertEqual(self.teacher_client.get("/api/auth/teacher-me").status_code, 200)
        self.assertEqual(old_teacher_client.get("/api/auth/teacher-me").status_code, 401)
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
        imported_exam = response.json()["exam"]
        exam_id = imported_exam["id"]
        original_question_id = imported_exam["questions"][0]["id"]

        response = self._json(
            "post",
            "/api/teacher/exams/import-json/",
            {
                "action": "update_exam",
                "exam_id": exam_id,
                "exam": {},
                "replace_questions": False,
                "questions": [
                    {
                        "action": "create",
                        "position": 2,
                        "question_type": "table",
                        "prompt": "Initial table prompt",
                        "max_score": 4,
                        "table_schema": {
                            "columns": [{"label": "A"}, {"label": "B"}],
                            "rows": [
                                {
                                    "cells": [
                                        {"mode": "given", "value": "fixed"},
                                        {"mode": "input", "answer": "first answer"},
                                    ]
                                }
                            ],
                        },
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        table_question = next(
            question
            for question in response.json()["exam"]["questions"]
            if question["question_type"] == "table"
        )

        response = self._json(
            "post",
            "/api/teacher/exams/import-json/",
            {
                "action": "update_exam",
                "exam_id": exam_id,
                "exam": {},
                "replace_questions": False,
                "questions": [
                    {
                        "action": "update",
                        "id": table_question["id"],
                        "prompt": "Edited table prompt",
                        "table_schema": {
                            "columns": [{"label": "Term"}, {"label": "Meaning"}],
                            "rows": [
                                {
                                    "cells": [
                                        {"mode": "given", "value": "TCP"},
                                        {"mode": "input", "answer": "Reliable"},
                                    ]
                                }
                            ],
                        },
                    },
                    {"action": "delete", "id": original_question_id},
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        edited_questions = response.json()["exam"]["questions"]
        self.assertEqual(len(edited_questions), 1)
        self.assertEqual(edited_questions[0]["prompt"], "Edited table prompt")
        self.assertEqual(
            edited_questions[0]["table_schema"]["rows"][0]["cells"][1]["answer"],
            "Reliable",
        )

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


class ModuleJsonImportTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(
            full_name="Module Teacher",
            pin_hash="!",
            is_active=True,
        )
        self.session = Session.objects.create(
            title="JSON module session",
            author=self.teacher,
        )
        self.client = Client()
        teacher_session = self.client.session
        teacher_session["teacher_id"] = self.teacher.id
        teacher_session["teacher_auth_version"] = auth_version(self.teacher.pin_hash)
        teacher_session.save()

    def _import(self, payload, session=None):
        session = session or self.session
        return self.client.post(
            f"/api/teacher/sessions/{session.id}/modules/import-json/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_imports_every_module_shape_with_nested_content(self):
        payload = {
            "action": "create_modules",
            "modules": [
                {
                    "module_type": "coding_task",
                    "position": 1,
                    "title": "Coding",
                    "statement": "Read and print a value.",
                    "programming_language": "cpp",
                    "hints": {
                        "enabled": True,
                        "level_1": {"enabled": True, "unlock_attempts": 2},
                        "level_2": {"enabled": True, "unlock_attempts": 3},
                        "level_3": {"enabled": False, "unlock_attempts": 4},
                    },
                    "testcases": [
                        {
                            "ordinal": 1,
                            "stdin": "7\n",
                            "expected_stdout": "7\n",
                            "is_visible": True,
                        }
                    ],
                    "fragments": [
                        {
                            "position": "top",
                            "title": "Header",
                            "code": "#include <iostream>",
                            "is_active": True,
                        }
                    ],
                },
                {
                    "module_type": "theory_material",
                    "position": 2,
                    "title": "Theory",
                    "blocks": [
                        {
                            "ordinal": 1,
                            "block_type": "heading",
                            "heading_level": "h1",
                            "content": "Files",
                        },
                        {
                            "ordinal": 2,
                            "block_type": "attachment",
                            "content": "https://drive.google.com/file/d/test_file_id/view",
                        },
                    ],
                },
                {
                    "module_type": "theory_quiz",
                    "position": 3,
                    "title": "Quiz",
                    "questions": [
                        {
                            "ordinal": 1,
                            "question_type": "single_choice",
                            "prompt": "Choose one",
                            "choices": [
                                {"ordinal": 1, "text": "A", "is_correct": True},
                                {"ordinal": 2, "text": "B", "is_correct": False},
                                {"ordinal": 3, "text": "C", "is_correct": False},
                            ],
                        },
                        {
                            "ordinal": 2,
                            "question_type": "open_answer",
                            "prompt": "Explain",
                            "model_answer": "Explanation",
                            "accept_suitable_answer": True,
                        },
                        {
                            "ordinal": 3,
                            "question_type": "matching",
                            "prompt": "Match",
                            "pairs": [
                                {"ordinal": 1, "left_text": "A", "right_text": "1"},
                                {"ordinal": 2, "left_text": "B", "right_text": "2"},
                            ],
                        },
                    ],
                },
                {
                    "module_type": "game",
                    "position": 4,
                    "title": "Race",
                    "rubric": "mind_race",
                    "prompts": [
                        {
                            "ordinal": 1,
                            "sentence": "Functions use the def keyword.",
                            "missing_text": "def",
                        }
                    ],
                },
                {
                    "module_type": "game",
                    "position": "auto",
                    "title": "Wonder",
                    "rubric": "wonder_field",
                    "questions": [
                        {"ordinal": 1, "prompt": "Reusable code", "answer": "function"}
                    ],
                },
                {
                    "module_type": "game",
                    "position": "auto",
                    "title": "Tournament",
                    "rubric": "tournament",
                    "stages": [
                        {
                            "ordinal": 1,
                            "title": "Final",
                            "question_count": 1,
                            "questions": [
                                {"ordinal": 1, "prompt": "Output function", "answer": "print"}
                            ],
                        }
                    ],
                },
            ],
        }
        response = self._import(payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["created_count"], 6)

        task = SessionTask.objects.get(session=self.session)
        self.assertEqual(task.programming_language, "cpp")
        self.assertTrue(task.hints_enabled)
        self.assertFalse(task.hint3_enabled)
        self.assertEqual(TaskTestCase.objects.filter(task=task).count(), 1)
        self.assertEqual(TaskCodeFragment.objects.filter(task=task).count(), 1)

        theory = TheoryMaterialModule.objects.get(session=self.session)
        attachment = theory.blocks.get(block_type=TheoryMaterialBlock.BlockType.ATTACHMENT)
        self.assertIn("drive.google.com", attachment.content)

        quiz = TheoryQuizModule.objects.get(session=self.session)
        self.assertEqual(quiz.questions.count(), 3)
        self.assertEqual(quiz.questions.get(ordinal=1).choices.count(), 3)
        self.assertEqual(quiz.questions.get(ordinal=3).pairs.count(), 2)

        self.assertEqual(GameModule.objects.filter(session=self.session).count(), 3)
        self.assertEqual(MindRacePrompt.objects.count(), 1)
        self.assertEqual(WonderFieldQuestion.objects.count(), 1)
        self.assertEqual(TournamentStage.objects.count(), 1)
        self.assertEqual(
            list(GameModule.objects.filter(session=self.session).values_list("position", flat=True)),
            [4, 5, 6],
        )

    def test_invalid_nested_item_rolls_back_entire_import(self):
        response = self._import(
            {
                "action": "create_modules",
                "modules": [
                    {
                        "module_type": "theory_material",
                        "position": 1,
                        "title": "Would otherwise be valid",
                        "blocks": [{"ordinal": 1, "block_type": "text", "content": "Text"}],
                    },
                    {
                        "module_type": "theory_material",
                        "position": 2,
                        "title": "Invalid attachment",
                        "blocks": [
                            {
                                "ordinal": 1,
                                "block_type": "attachment",
                                "content": "https://example.com/not-drive.pdf",
                            }
                        ],
                    },
                ],
            }
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TheoryMaterialModule.objects.filter(session=self.session).count(), 0)

    def test_cannot_import_into_another_teachers_session(self):
        other_teacher = Teacher.objects.create(
            full_name="Other Module Teacher",
            pin_hash="!",
            is_active=True,
        )
        other_session = Session.objects.create(title="Private", author=other_teacher)
        response = self._import(
            {
                "action": "create_modules",
                "modules": [
                    {
                        "module_type": "theory_material",
                        "title": "Forbidden",
                        "blocks": [],
                    }
                ],
            },
            session=other_session,
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(TheoryMaterialModule.objects.filter(session=other_session).exists())
