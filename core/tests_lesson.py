import json

from django.test import Client, TestCase

from .models import (ClassGroup, LessonHighlight, Session, Student, StudentTheoryQuizAttempt,
                     Teacher, TheoryMaterialBlock, TheoryMaterialModule, TheoryQuizChoice,
                     TheoryQuizMatchPair, TheoryQuizModule, TheoryQuizQuestion)
from .security import auth_version


class LessonTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(full_name="Teacher", pin_hash="!", is_active=True)
        auth = self.client.session
        auth.update({"teacher_id": self.teacher.id, "teacher_auth_version": auth_version("!")})
        auth.save()
        self.group = ClassGroup.objects.create(name="A", owner=self.teacher)
        self.other_group = ClassGroup.objects.create(name="B", owner=self.teacher)
        self.lesson = Session.objects.create(title="Lesson", author=self.teacher, status="running")
        self.lesson.allowed_classes.add(self.group, self.other_group)
        self.module = TheoryMaterialModule.objects.create(session=self.lesson, position=1, title="Theory")
        self.block = TheoryMaterialBlock.objects.create(module=self.module, ordinal=1, block_type="text", content="Hello 🌍 world")
        self.url = f"/api/teacher/theory-modules/{self.module.id}/highlights/?class_id={self.group.id}"
        self.student_url = f"/api/student/theory-module/{self.module.id}/highlights/"

    def student_client(self, group):
        student = Student.objects.create(full_name="Pupil", class_group=group, pin_hash="!", is_active=True)
        client = Client()
        auth = client.session
        auth.update({"student_id": student.id, "student_class_id": group.id, "student_auth_version": auth_version("!")})
        auth.save()
        return client

    def put(self, ranges):
        return self.client.put(self.url, json.dumps({"ranges": ranges}), content_type="application/json")

    def test_highlights_survive_reload_and_are_scoped_to_class(self):
        self.assertEqual(self.put([{"block_id": self.block.id, "start": 6, "end": 8}]).status_code, 200)
        saved = self.client.get(self.url).json()["ranges"]
        self.assertEqual(saved[0]["text"], self.block.content)
        self.assertEqual(self.student_client(self.group).get(self.student_url).json()["ranges"], saved)
        self.assertEqual(self.student_client(self.other_group).get(self.student_url).json()["ranges"], [])
        self.assertEqual(self.put([]).json()["ranges"], [])

    def test_invalid_ranges_do_not_overwrite_markers(self):
        self.put([{"block_id": self.block.id, "start": 0, "end": 5}])
        for ranges in ([{"block_id": 9999, "start": 0, "end": 1}], [None],
                       [{"block_id": self.block.id, "start": -1, "end": 5}],
                       [{"block_id": self.block.id, "start": 0, "end": 99}], "bad"):
            self.assertEqual(self.put(ranges).status_code, 400)
        self.assertEqual(LessonHighlight.objects.get().ranges[0]["end"], 5)

    def test_permissions(self):
        self.assertEqual(Client().get(self.url).status_code, 401)
        self.assertEqual(Client().get(self.student_url).status_code, 401)
        outsider = Teacher.objects.create(full_name="Other", pin_hash="!")
        self.lesson.author = outsider
        self.lesson.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(f"/teacher/sessions/{self.lesson.id}/lesson/").status_code, 404)

    def test_student_without_session_access_cannot_read_highlights(self):
        unassigned = ClassGroup.objects.create(name="Other", owner=self.teacher)
        self.assertEqual(self.student_client(unassigned).get(self.student_url).status_code, 403)

    def test_teacher_practice_uses_student_grading_without_student_attempts(self):
        quiz = TheoryQuizModule.objects.create(session=self.lesson, position=2, title="Quiz")
        choice_q = TheoryQuizQuestion.objects.create(module=quiz, ordinal=1, question_type="single_choice", prompt="Select")
        choice = TheoryQuizChoice.objects.create(question=choice_q, ordinal=1, text="Yes", is_correct=True)
        open_q = TheoryQuizQuestion.objects.create(module=quiz, ordinal=2, question_type="open_answer", prompt="Define", model_answer="Value")
        match_q = TheoryQuizQuestion.objects.create(module=quiz, ordinal=3, question_type="matching", prompt="Match")
        TheoryQuizMatchPair.objects.create(question=match_q, ordinal=1, left_text="Left", right_text="Right")
        url = f"/api/teacher/theory-quizzes/{quiz.id}/practice/"
        public = self.client.get(url).json()["module"]["questions"]
        self.assertNotIn("is_correct", public[0]["choices"][0])
        self.assertNotIn("model_answer", public[1])
        answers = {str(choice_q.id): choice.id, str(open_q.id): "value", str(match_q.id): {
            public[2]["left_items"][0]["id"]: public[2]["right_items"][0]["id"]}}
        result = self.client.post(url, json.dumps({"answers": answers}), content_type="application/json").json()
        self.assertEqual(result["attempt"]["score_percent"], 100)
        self.assertFalse(StudentTheoryQuizAttempt.objects.exists())
        self.assertEqual(Client().get(url).status_code, 401)

    def test_game_controls_only_on_lesson_page(self):
        lesson = self.client.get(f"/teacher/sessions/{self.lesson.id}/lesson/")
        self.assertContains(lesson, 'id="gameOpenLobbyBtn"', count=1)
        self.assertContains(lesson, 'id="quizQuestions"', count=1)
        editor = self.client.get("/teacher/modules/")
        self.assertNotContains(editor, 'id="gameOpenLobbyBtn"')
        self.assertContains(editor, 'id="gameAddPromptBtn"')

    def test_bulk_import_skips_duplicates_and_creates_memberships(self):
        url = "/api/teacher/students/bulk/"
        payload = {"class_id": self.group.id, "pin": "123456", "names": "Алия\r\n\n Борис \nАлия"}
        result = self.client.post(url, json.dumps(payload), content_type="application/json")
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()["created_count"], 2)
        self.assertEqual(result.json()["skipped_count"], 1)
        for student in Student.objects.all():
            self.assertTrue(student.check_pin("123456"))
            self.assertTrue(student.class_groups.filter(id=self.group.id).exists())
        repeat = self.client.post(url, json.dumps(payload), content_type="application/json").json()
        self.assertEqual(repeat["created_count"], 0)

    def test_bulk_import_validates_every_name_and_class_before_writing(self):
        url = "/api/teacher/students/bulk/"
        payload = {"class_id": self.group.id, "pin": "123456", "names": "Good\n" + "x" * 121}
        self.assertEqual(self.client.post(url, json.dumps(payload), content_type="application/json").status_code, 400)
        self.assertFalse(Student.objects.exists())
        outsider = Teacher.objects.create(full_name="Other", pin_hash="!")
        group = ClassGroup.objects.create(name="Other", owner=outsider)
        payload.update(class_id=group.id, names="Good")
        self.assertEqual(self.client.post(url, json.dumps(payload), content_type="application/json").status_code, 404)
        self.assertEqual(Client().post(url, json.dumps(payload), content_type="application/json").status_code, 401)
