import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from .models import (
    ClassGroup,
    GameModule,
    GameParticipant,
    GameRound,
    Session,
    SessionClass,
    SessionTask,
    Student,
    Teacher,
)
from .security import auth_version


class MindRaceGameTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(full_name="Game Teacher", pin_hash="!", is_active=True)
        self.class_a = ClassGroup.objects.create(name="Class A", owner=self.teacher)
        self.class_b = ClassGroup.objects.create(name="Class B", owner=self.teacher)
        self.student_a = Student.objects.create(
            full_name="Runner A",
            class_group=self.class_a,
            pin_hash="!",
            is_active=True,
        )
        self.student_a2 = Student.objects.create(
            full_name="Runner A2",
            class_group=self.class_a,
            pin_hash="!",
            is_active=True,
        )
        self.student_b = Student.objects.create(
            full_name="Runner B",
            class_group=self.class_b,
            pin_hash="!",
            is_active=True,
        )
        now = timezone.now()
        self.session = Session.objects.create(
            title="Live lesson",
            author=self.teacher,
            status=Session.Status.RUNNING,
            starts_at=now - timedelta(minutes=10),
            ends_at=now + timedelta(hours=1),
        )
        SessionClass.objects.create(session=self.session, class_group=self.class_a)
        SessionClass.objects.create(session=self.session, class_group=self.class_b)
        self.teacher_client = self._teacher_client(self.teacher)
        self.client_a = self._student_client(self.student_a)
        self.client_a2 = self._student_client(self.student_a2)
        self.client_b = self._student_client(self.student_b)

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

    def _create_module(self):
        response = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/sessions/{self.session.id}/game-modules/",
            {"title": "Python race", "topic": "Collections", "position": 1, "rubric": "mind_race"},
        )
        self.assertEqual(response.status_code, 201)
        module_id = response.json()["module"]["id"]
        prompts = [
            {"ordinal": 1, "sentence": "Python uses lists for ordered data.", "missing_text": "lists"},
            {"ordinal": 2, "sentence": "A dictionary stores key value pairs.", "missing_text": "dictionary"},
        ]
        for prompt in prompts:
            added = self._json(
                self.teacher_client,
                "post",
                f"/api/teacher/game-modules/{module_id}/prompts/",
                prompt,
            )
            self.assertEqual(added.status_code, 201)
        return module_id

    def _open_round(self, module_id, class_group):
        response = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-modules/{module_id}/rounds/open/",
            {"class_id": class_group.id},
        )
        self.assertIn(response.status_code, {200, 201})
        return response.json()["round"]

    def test_rounds_are_isolated_by_class_and_repeat_run(self):
        module_id = self._create_module()
        round_a = self._open_round(module_id, self.class_a)

        ready_a = self._json(self.client_a, "post", f"/api/student/game-module/{module_id}/ready/")
        ready_a2 = self._json(self.client_a2, "post", f"/api/student/game-module/{module_id}/ready/")
        self.assertEqual(ready_a.status_code, 200)
        self.assertEqual(ready_a2.status_code, 200)
        blocked_b = self._json(self.client_b, "post", f"/api/student/game-module/{module_id}/ready/")
        self.assertEqual(blocked_b.status_code, 409)

        round_b = self._open_round(module_id, self.class_b)
        ready_b = self._json(self.client_b, "post", f"/api/student/game-module/{module_id}/ready/")
        self.assertEqual(ready_b.status_code, 200)
        self.assertNotEqual(round_a["id"], round_b["id"])

        started = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-rounds/{round_a['id']}/start/",
        )
        self.assertEqual(started.status_code, 200)
        self.assertEqual(GameRound.objects.get(id=round_b["id"]).status, GameRound.Status.LOBBY)

        state_a = self.client_a.get(f"/api/student/game-rounds/{round_a['id']}/state/")
        self.assertEqual(state_a.status_code, 200)
        content = state_a.content.decode("utf-8").casefold()
        self.assertNotIn('"answer"', content)
        self.assertNotIn("lists", state_a.json()["round"]["current_prompt"]["sentence"].casefold())

        wrong = self._json(
            self.client_a,
            "post",
            f"/api/student/game-rounds/{round_a['id']}/answer/",
            {"prompt_index": 0, "answer": "tuple"},
        )
        self.assertEqual(wrong.status_code, 200)
        self.assertFalse(wrong.json()["correct"])

        correct = self._json(
            self.client_a,
            "post",
            f"/api/student/game-rounds/{round_a['id']}/answer/",
            {"prompt_index": 0, "answer": "LISTS"},
        )
        self.assertTrue(correct.json()["correct"])
        stale = self._json(
            self.client_a,
            "post",
            f"/api/student/game-rounds/{round_a['id']}/answer/",
            {"prompt_index": 0, "answer": "lists"},
        )
        self.assertTrue(stale.json()["stale"])
        self.assertEqual(GameParticipant.objects.get(round_id=round_a["id"], student=self.student_a).progress, 1)

        finish = self._json(
            self.client_a,
            "post",
            f"/api/student/game-rounds/{round_a['id']}/answer/",
            {"prompt_index": 1, "answer": "dictionary"},
        )
        self.assertTrue(finish.json()["finished"])
        self.assertEqual(finish.json()["place"], 1)

        ended = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-rounds/{round_a['id']}/finish/",
        )
        self.assertEqual(ended.status_code, 200)
        places = list(
            GameParticipant.objects.filter(round_id=round_a["id"])
            .order_by("finish_place")
            .values_list("finish_place", flat=True)
        )
        self.assertEqual(places, [1, 2])

        repeated = self._open_round(module_id, self.class_a)
        self.assertEqual(repeated["run_number"], 2)
        self.assertEqual(GameRound.objects.get(id=round_a["id"]).status, GameRound.Status.FINISHED)
        self.assertEqual(GameParticipant.objects.filter(round_id=round_a["id"]).count(), 2)

    def test_prompt_validation_and_shared_position_validation(self):
        SessionTask.objects.create(
            session=self.session,
            position=1,
            title="Existing task",
            statement="Solve",
        )
        conflict = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/sessions/{self.session.id}/game-modules/",
            {"title": "Conflict", "position": 1},
        )
        self.assertEqual(conflict.status_code, 409)

        created = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/sessions/{self.session.id}/game-modules/",
            {"title": "Valid", "position": 2},
        )
        module_id = created.json()["module"]["id"]
        invalid = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-modules/{module_id}/prompts/",
            {"ordinal": 1, "sentence": "Python is readable.", "missing_text": "Java"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("must occur", invalid.json()["error"])

    def test_unregistered_student_cannot_answer_running_round(self):
        module_id = self._create_module()
        round_a = self._open_round(module_id, self.class_a)
        self._json(self.client_a, "post", f"/api/student/game-module/{module_id}/ready/")
        self._json(self.teacher_client, "post", f"/api/teacher/game-rounds/{round_a['id']}/start/")
        response = self._json(
            self.client_a2,
            "post",
            f"/api/student/game-rounds/{round_a['id']}/answer/",
            {"prompt_index": 0, "answer": "lists"},
        )
        self.assertEqual(response.status_code, 404)

    def test_teacher_ownership_and_student_module_list(self):
        module_id = self._create_module()
        other_teacher = Teacher.objects.create(full_name="Other Game Teacher", pin_hash="!", is_active=True)
        other_client = self._teacher_client(other_teacher)
        hidden = other_client.get(f"/api/teacher/game-modules/{module_id}/")
        self.assertEqual(hidden.status_code, 404)

        module_list = self.client_a.get(f"/api/student/active-session?session_id={self.session.id}")
        self.assertEqual(module_list.status_code, 200)
        game_rows = [row for row in module_list.json()["tasks"] if row["module_type"] == "game"]
        self.assertEqual(len(game_rows), 1)
        self.assertEqual(game_rows[0]["id"], module_id)

        teacher_page = self.teacher_client.get("/teacher/modules/")
        self.assertEqual(teacher_page.status_code, 200)
        self.assertContains(teacher_page, "/game-modules/")
        student_page = self.client_a.get("/student/")
        self.assertEqual(student_page.status_code, 200)
        self.assertContains(student_page, 'id="gameCard"')
