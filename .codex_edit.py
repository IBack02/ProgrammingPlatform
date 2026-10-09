from pathlib import Path
p=Path('core/tests_game_modules.py')
s=p.read_text(encoding='utf-8');s+='''
    def test_tournament_preparation_blocks_answers_and_hides_question_for_three_seconds(self):
        from .game_views import _create_tournament_matches
        round_obj, match, players = self._cooldown_tournament()
        match.delete()
        now = timezone.now()
        with patch("core.game_views.timezone.now", return_value=now) as clock:
            _create_tournament_matches(round_obj, 1, players)
            match = round_obj.tournament_matches.get()
            self.assertEqual(match.started_at, now + timedelta(seconds=3))
            url = f"/api/student/game-rounds/{round_obj.id}/tournament-answer/"
            payload = {"match_id": match.id, "question_index": 0, "answer": "Answer 1"}
            for elapsed, remaining in [(0, 3000), (1000, 2000), (2999, 1)]:
                clock.return_value = now + timedelta(milliseconds=elapsed)
                for client in (self.client_a, self.client_a2):
                    state = client.get(f"/api/student/game-rounds/{round_obj.id}/state/").json()["round"]
                    self.assertEqual(state["tournament_preparation_ms"], remaining)
                    self.assertFalse(state["can_answer_tournament"])
                    self.assertIsNone(state["tournament_matches"][0]["current_question"])
                    response = self._json(client, "post", url, payload)
                    self.assertEqual(response.status_code, 409)
                    self.assertTrue(response.json()["preparing"])
                self.assertFalse(TournamentAnswerAttempt.objects.exists())
            clock.return_value = now + timedelta(seconds=3)
            result = self._json(self.client_a, "post", url, payload)
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json()["correct"])

    def test_next_stage_prepares_and_eliminated_students_only_receive_bracket(self):
        from .game_views import _advance_tournament
        round_obj, first, players = self._cooldown_tournament()
        round_obj.prompt_snapshot = round_obj.prompt_snapshot * 2
        round_obj.save()
        third = Student.objects.create(full_name="Third", class_group=self.class_a, pin_hash="!")
        third_player = GameParticipant.objects.create(round=round_obj, student=third)
        first.status = TournamentMatch.Status.FINISHED
        first.winner = players[0]
        first.save()
        TournamentMatch.objects.create(round=round_obj, stage_number=1, match_number=2,
            player_one=third_player, winner=third_player, status=TournamentMatch.Status.FINISHED)
        now = timezone.now()
        with patch("core.game_views.timezone.now", return_value=now) as clock:
            _advance_tournament(round_obj)
            final = round_obj.tournament_matches.get(stage_number=2)
            self.assertEqual(final.started_at, now + timedelta(seconds=3))
            clock.return_value = now + timedelta(seconds=3)
            url = f"/api/student/game-rounds/{round_obj.id}/state/"
            eliminated = self.client_a2.get(url).json()["round"]
            self.assertTrue(eliminated["tournament_eliminated"])
            self.assertEqual(len(eliminated["tournament_matches"]), 3)
            self.assertTrue(all(row["current_question"] is None for row in eliminated["tournament_matches"]))
            self.assertNotIn("tournament_answer_attempts", eliminated)
            self.assertFalse(eliminated["can_answer_tournament"])
            teacher = self.teacher_client.get(f"/api/teacher/game-rounds/{round_obj.id}/state/").json()["round"]
            self.assertTrue(next(row for row in teacher["tournament_matches"] if row["id"] == final.id)["current_question"])
            forbidden = self._json(self.client_a2, "post", f"/api/student/game-rounds/{round_obj.id}/tournament-answer/",
                {"match_id": final.id, "question_index": 0, "answer": "Answer 1"})
            self.assertEqual(forbidden.status_code, 403)
''';p.write_text(s,encoding='utf-8')
p=Path('core/tests_lesson.py');s=p.read_text(encoding='utf-8');s+='''
    def test_marker_palette_colors_are_saved_and_shared(self):
        student = self.student_client(self.group)
        for color in ("yellow", "green", "blue", "pink", "purple", "orange", "red"):
            result = self.put([{"block_id": self.block.id, "start": 0, "end": 5, "color": color}])
            self.assertEqual(result.status_code, 200)
            self.assertEqual(student.get(self.student_url).json()["ranges"][0]["color"], color)
        self.assertEqual(self.put([{"block_id": self.block.id, "start": 0, "end": 5, "color": "invalid"}]).status_code, 400)
        self.assertEqual(self.put([{"block_id": self.block.id, "start": 0, "end": 5}]).json()["ranges"][0]["color"], "yellow")
''';p.write_text(s,encoding='utf-8')
