import json
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from .models import (
    ClassGroup,
    GameModule,
    GameParticipant,
    GameRound,
    GameRoundEvent,
    Session,
    SessionClass,
    SessionTask,
    Student,
    Teacher,
    TournamentMatch,
    WonderFieldQuestion,
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

    def _create_wonder_module(self):
        response = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/sessions/{self.session.id}/game-modules/",
            {"title": "Wonder", "topic": "Terms", "position": 1, "rubric": "wonder_field"},
        )
        self.assertEqual(response.status_code, 201)
        module_id = response.json()["module"]["id"]
        for question in [
            {"ordinal": 1, "prompt": "Two letters", "answer": "A B"},
            {"ordinal": 2, "prompt": "Programming unit", "answer": "CODE"},
        ]:
            added = self._json(
                self.teacher_client,
                "post",
                f"/api/teacher/game-modules/{module_id}/wonder-questions/",
                question,
            )
            self.assertEqual(added.status_code, 201)
        return module_id

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

    def test_wonder_field_uses_fixed_turns_and_never_exposes_answer(self):
        module_id = self._create_wonder_module()
        round_row = self._open_round(module_id, self.class_a)
        self._json(self.client_a, "post", f"/api/student/game-module/{module_id}/ready/")
        self._json(self.client_a2, "post", f"/api/student/game-module/{module_id}/ready/")
        started = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-rounds/{round_row['id']}/start/",
        )
        self.assertEqual(started.status_code, 200)
        started_round = started.json()["round"]
        original_order = [row["student_id"] for row in started_round["turn_order"]]
        self.assertCountEqual(original_order, [self.student_a.id, self.student_a2.id])

        clients = {self.student_a.id: self.client_a, self.student_a2.id: self.client_a2}
        first_student_id = started_round["current_turn_student_id"]
        other_student_id = next(value for value in original_order if value != first_student_id)
        blocked = self._json(
            clients[other_student_id],
            "post",
            f"/api/student/game-rounds/{round_row['id']}/letter/",
            {"letter": "A", "question_index": 0},
        )
        self.assertEqual(blocked.status_code, 409)

        first_guess = self._json(
            clients[first_student_id],
            "post",
            f"/api/student/game-rounds/{round_row['id']}/letter/",
            {"letter": "A", "question_index": 0},
        )
        self.assertEqual(first_guess.status_code, 200)
        self.assertTrue(first_guess.json()["correct"])
        state = first_guess.json()["round"]
        self.assertEqual([cell["kind"] for cell in state["current_question"]["cells"]], ["letter", "space", "letter"])
        self.assertEqual(state["current_question"]["cells"][0]["value"], "A")
        expected_name = {self.student_a.id: self.student_a.full_name, self.student_a2.id: self.student_a2.full_name}
        self.assertEqual(state["current_question"]["cells"][0]["guessed_by"], [expected_name[first_student_id]])

        second_student_id = state["current_turn_student_id"]
        completed = self._json(
            clients[second_student_id],
            "post",
            f"/api/student/game-rounds/{round_row['id']}/letter/",
            {"letter": "B", "question_index": 0},
        )
        self.assertTrue(completed.json()["question_complete"])
        next_state = completed.json()["round"]
        self.assertEqual(next_state["current_question_index"], 1)
        self.assertEqual(next_state["used_letters"], [])
        self.assertEqual([row["student_id"] for row in next_state["turn_order"]], original_order)

        student_state = clients[first_student_id].get(f"/api/student/game-rounds/{round_row['id']}/state/")
        self.assertNotIn('"answer"', student_state.content.decode("utf-8").casefold())
        self.assertNotIn("code", student_state.content.decode("utf-8").casefold())

    def test_wonder_field_timeout_and_three_strikes_end_the_game(self):
        module_id = self._create_wonder_module()
        round_row = self._open_round(module_id, self.class_a)
        self._json(self.client_a, "post", f"/api/student/game-module/{module_id}/ready/")
        self._json(self.teacher_client, "post", f"/api/teacher/game-rounds/{round_row['id']}/start/")
        GameRound.objects.filter(id=round_row["id"]).update(
            turn_started_at=timezone.now() - timedelta(seconds=21),
        )

        timed_out = self.client_a.get(f"/api/student/game-rounds/{round_row['id']}/state/")
        self.assertEqual(timed_out.status_code, 200)
        self.assertEqual(timed_out.json()["round"]["strikes"], 1)
        self.assertTrue(GameRoundEvent.objects.filter(round_id=round_row["id"], event_type="timeout").exists())

        for _ in range(2):
            penalty = self._json(
                self.teacher_client,
                "post",
                f"/api/teacher/game-rounds/{round_row['id']}/penalty/",
            )
        self.assertEqual(penalty.status_code, 200)
        self.assertEqual(penalty.json()["round"]["status"], GameRound.Status.FINISHED)
        self.assertEqual(penalty.json()["round"]["outcome"], GameRound.Outcome.LOST)

    def test_teacher_can_remove_wonder_field_penalty(self):
        module_id = self._create_wonder_module()
        round_row = self._open_round(module_id, self.class_a)
        self._json(self.client_a, "post", f"/api/student/game-module/{module_id}/ready/")
        self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-rounds/{round_row['id']}/start/",
        )

        added = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-rounds/{round_row['id']}/penalty/",
        )
        self.assertEqual(added.status_code, 200)
        self.assertEqual(added.json()["round"]["strikes"], 1)

        removed = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-rounds/{round_row['id']}/penalty/remove/",
        )
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.json()["round"]["strikes"], 0)
        self.assertTrue(
            GameRoundEvent.objects.filter(
                round_id=round_row["id"],
                event_type=GameRoundEvent.EventType.PENALTY_REMOVED,
            ).exists()
        )

        removed_again = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-rounds/{round_row['id']}/penalty/remove/",
        )
        self.assertEqual(removed_again.status_code, 200)
        self.assertEqual(removed_again.json()["round"]["strikes"], 0)
        self.assertEqual(
            GameRoundEvent.objects.filter(
                round_id=round_row["id"],
                event_type=GameRoundEvent.EventType.PENALTY_REMOVED,
            ).count(),
            1,
        )

    def test_wonder_question_validation(self):
        module = GameModule.objects.create(
            session=self.session,
            position=1,
            title="Wonder",
            rubric=GameModule.Rubric.WONDER_FIELD,
        )
        accepted = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-modules/{module.id}/wonder-questions/",
            {"ordinal": 1, "prompt": "Question", "answer": "Жауап C++ №1"},
        )
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(accepted.json()["question"]["answer"], "ЖАУАП C++ №1")

        invalid = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-modules/{module.id}/wonder-questions/",
            {"ordinal": 2, "prompt": "Hidden character", "answer": "CODE\u200b"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(WonderFieldQuestion.objects.filter(module=module).count(), 1)

    def test_wonder_field_supports_cyrillic_and_symbols(self):
        module = GameModule.objects.create(
            session=self.session,
            position=1,
            title="Unicode wonder",
            rubric=GameModule.Rubric.WONDER_FIELD,
        )
        WonderFieldQuestion.objects.create(
            module=module,
            ordinal=1,
            prompt="Programming language",
            answer="КОД C++ №1",
        )
        round_row = self._open_round(module.id, self.class_a)
        self._json(self.client_a, "post", f"/api/student/game-module/{module.id}/ready/")
        started = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-rounds/{round_row['id']}/start/",
        )
        self.assertEqual(started.status_code, 200)
        state = started.json()["round"]
        for character in ["К", "C", "+", "№", "1"]:
            self.assertIn(character, state["keyboard_characters"])

        guessed = self._json(
            self.client_a,
            "post",
            f"/api/student/game-rounds/{round_row['id']}/letter/",
            {"letter": "к", "question_index": 0},
        )
        self.assertEqual(guessed.status_code, 200)
        self.assertTrue(guessed.json()["correct"])
        self.assertEqual(guessed.json()["round"]["current_question"]["cells"][0]["value"], "К")

        unavailable = self._json(
            self.client_a,
            "post",
            f"/api/student/game-rounds/{round_row['id']}/letter/",
            {"letter": "$", "question_index": 0},
        )
        self.assertEqual(unavailable.status_code, 409)

    def test_wonder_questions_can_shuffle_only_before_start(self):
        module_id = self._create_wonder_module()
        original_ids = list(
            WonderFieldQuestion.objects.filter(module_id=module_id)
            .order_by("ordinal")
            .values_list("id", flat=True)
        )
        with patch("core.game_views.secrets.SystemRandom.shuffle", side_effect=lambda rows: rows.reverse()):
            shuffled = self._json(
                self.teacher_client,
                "post",
                f"/api/teacher/game-modules/{module_id}/wonder-questions/shuffle/",
            )
        self.assertEqual(shuffled.status_code, 200)
        self.assertEqual(
            [row["id"] for row in shuffled.json()["questions"]],
            list(reversed(original_ids)),
        )
        self.assertEqual(
            list(
                WonderFieldQuestion.objects.filter(module_id=module_id)
                .order_by("ordinal")
                .values_list("ordinal", flat=True)
            ),
            [1, 2],
        )

        round_row = self._open_round(module_id, self.class_a)
        self._json(self.client_a, "post", f"/api/student/game-module/{module_id}/ready/")
        self._json(self.teacher_client, "post", f"/api/teacher/game-rounds/{round_row['id']}/start/")
        blocked = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-modules/{module_id}/wonder-questions/shuffle/",
        )
        self.assertEqual(blocked.status_code, 409)

    def test_empty_game_module_rubric_can_be_changed(self):
        module = GameModule.objects.create(
            session=self.session,
            position=1,
            title="Empty game",
            rubric=GameModule.Rubric.MIND_RACE,
        )
        changed = self._json(
            self.teacher_client,
            "patch",
            f"/api/teacher/game-modules/{module.id}/",
            {"rubric": "wonder_field"},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["module"]["rubric"], GameModule.Rubric.WONDER_FIELD)

        WonderFieldQuestion.objects.create(module=module, ordinal=1, prompt="Question", answer="ANSWER")
        blocked = self._json(
            self.teacher_client,
            "patch",
            f"/api/teacher/game-modules/{module.id}/",
            {"rubric": "mind_race"},
        )
        self.assertEqual(blocked.status_code, 409)
        module.refresh_from_db()
        self.assertEqual(module.rubric, GameModule.Rubric.WONDER_FIELD)

    def test_tournament_builds_single_elimination_bracket_with_bye(self):
        student_a3 = Student.objects.create(
            full_name="Runner A3",
            class_group=self.class_a,
            pin_hash="!",
            is_active=True,
        )
        client_a3 = self._student_client(student_a3)
        clients = {
            self.student_a.id: self.client_a,
            self.student_a2.id: self.client_a2,
            student_a3.id: client_a3,
        }
        created = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/sessions/{self.session.id}/game-modules/",
            {"title": "Cup", "position": 1, "rubric": "tournament"},
        )
        self.assertEqual(created.status_code, 201)
        module_id = created.json()["module"]["id"]
        for stage_ordinal in (1, 2):
            stage_response = self._json(
                self.teacher_client,
                "post",
                f"/api/teacher/game-modules/{module_id}/tournament-stages/",
                {"ordinal": stage_ordinal, "title": f"Stage {stage_ordinal}", "question_count": 3},
            )
            self.assertEqual(stage_response.status_code, 201)
            stage_id = stage_response.json()["stage"]["id"]
            for ordinal in (1, 2, 3):
                question = self._json(
                    self.teacher_client,
                    "post",
                    f"/api/teacher/tournament-stages/{stage_id}/questions/",
                    {"ordinal": ordinal, "prompt": f"S{stage_ordinal} Q{ordinal}", "answer": f"A{stage_ordinal}{ordinal}"},
                )
                self.assertEqual(question.status_code, 201)

        even_stage = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-modules/{module_id}/tournament-stages/",
            {"ordinal": 3, "question_count": 2},
        )
        self.assertEqual(even_stage.status_code, 400)

        round_row = self._open_round(module_id, self.class_a)
        for client in clients.values():
            self.assertEqual(
                self._json(client, "post", f"/api/student/game-module/{module_id}/ready/").status_code,
                200,
            )
        started = self._json(
            self.teacher_client,
            "post",
            f"/api/teacher/game-rounds/{round_row['id']}/start/",
        )
        self.assertEqual(started.status_code, 200)
        state = started.json()["round"]
        self.assertNotIn('"answer"', json.dumps(state).casefold())
        stage_one = [row for row in state["tournament_matches"] if row["stage_number"] == 1]
        self.assertEqual(len(stage_one), 2)
        self.assertEqual(sum(row["status"] == TournamentMatch.Status.FINISHED for row in stage_one), 1)
        active = next(row for row in stage_one if row["status"] == TournamentMatch.Status.RUNNING)
        winner_student_id = active["player_one"]["student_id"]
        for question_index in (0, 1):
            response = self._json(
                clients[winner_student_id],
                "post",
                f"/api/student/game-rounds/{round_row['id']}/tournament-answer/",
                {"match_id": active["id"], "question_index": question_index, "answer": f"A1{question_index + 1}"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["correct"])

        final_match = TournamentMatch.objects.get(round_id=round_row["id"], stage_number=2)
        final_student_id = final_match.player_one.student_id
        for question_index in (0, 1):
            response = self._json(
                clients[final_student_id],
                "post",
                f"/api/student/game-rounds/{round_row['id']}/tournament-answer/",
                {"match_id": final_match.id, "question_index": question_index, "answer": f"A2{question_index + 1}"},
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["round"]["status"], GameRound.Status.FINISHED)
        self.assertEqual(response.json()["round"]["me"]["finish_place"], 1)
