import json

from django.test import Client, TestCase

from .models import (
    GameModule, Session, SessionTask, Teacher, TheoryMaterialBlock,
    TheoryMaterialModule, TheoryQuizChoice, TheoryQuizMatchPair,
    TheoryQuizModule, TheoryQuizQuestion,
)
from .security import auth_version


class ModuleEditorTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(full_name="Editor", pin_hash="!", is_active=True)
        session = self.client.session
        session["teacher_id"] = self.teacher.id
        session["teacher_auth_version"] = auth_version(self.teacher.pin_hash)
        session.save()
        self.lesson = Session.objects.create(title="Lesson", author=self.teacher)
        self.task = SessionTask.objects.create(session=self.lesson, position=1, title="Code", statement="Solve")
        self.task2 = SessionTask.objects.create(session=self.lesson, position=2, title="Code 2", statement="Solve 2")
        self.theory = TheoryMaterialModule.objects.create(session=self.lesson, position=3, title="Theory")
        self.quiz = TheoryQuizModule.objects.create(session=self.lesson, position=4, title="Quiz")
        self.game = GameModule.objects.create(session=self.lesson, position=5, title="Game")

    def request_json(self, method, url, data):
        return getattr(self.client, method)(url, json.dumps(data), content_type="application/json")

    def test_reorder_mixed_modules_and_swap_unique_task_positions(self):
        order = [("game",self.game),("coding_task",self.task2),("theory_material",self.theory),
                 ("theory_quiz",self.quiz),("coding_task",self.task)]
        url = f"/api/teacher/sessions/{self.lesson.id}/modules/reorder/"
        for rows in (order, list(reversed(order))):
            response = self.request_json("post", url, {"items":[{"module_type":kind,"id":row.id} for kind,row in rows]})
            self.assertEqual(response.status_code, 200)
            for index, (_,row) in enumerate(rows, start=1):
                row.refresh_from_db()
                self.assertEqual(row.position, index)
        self.assertEqual(self.task.statement, "Solve")

    def test_bad_or_outdated_orders_do_not_change_any_positions(self):
        rows = [("coding_task",self.task),("coding_task",self.task2),("theory_material",self.theory),
                ("theory_quiz",self.quiz),("game",self.game)]
        items = [{"module_type":kind,"id":row.id} for kind,row in rows]
        url = f"/api/teacher/sessions/{self.lesson.id}/modules/reorder/"
        for payload, status in (({"items":items[:-1]},409), ({"items":items+[items[0]]},400),
                                ({"items":[{"module_type":"coding_task","id":99999}]+items[1:]},409),
                                ({"items":[{"module_type":"unknown","id":1}]},400),
                                ({"items":[True]},400), ({"items":"wrong"},400), ({},400)):
            with self.subTest(payload=payload):
                self.assertEqual(self.request_json("post",url,payload).status_code,status)
                for index,(_,row) in enumerate(rows,start=1):
                    row.refresh_from_db()
                    self.assertEqual(row.position,index)

    def test_child_reorder_preserves_content_and_choice_ids(self):
        blocks = [TheoryMaterialBlock.objects.create(module=self.theory,ordinal=i,block_type="text",content=f"Text {i}") for i in (1,2,3)]
        questions = [TheoryQuizQuestion.objects.create(module=self.quiz,ordinal=i,question_type="single_choice",prompt=f"Question {i}") for i in (1,2,3)]
        choice = TheoryQuizChoice.objects.create(question=questions[0],ordinal=1,text="Answer",is_correct=True)
        for path, module, rows in (("theory-modules",self.theory,blocks),("theory-quizzes",self.quiz,questions)):
            kind = "blocks" if path == "theory-modules" else "questions"
            url = f"/api/teacher/{path}/{module.id}/{kind}/reorder/"
            self.assertEqual(self.request_json("post",url,{"items":[row.id for row in reversed(rows)]}).status_code,200)
            for index,row in enumerate(reversed(rows),start=1):
                row.refresh_from_db()
                self.assertEqual(row.ordinal,index)
            self.assertEqual(self.request_json("post",url,{"items":[rows[0].id]}).status_code,409)
            self.assertEqual(self.request_json("post",url,{"items":[rows[0].id]*3}).status_code,400)
        choice.refresh_from_db()
        self.assertEqual(choice.text,"Answer")
        self.assertEqual(blocks[0].content,"Text 1")

    def test_only_owner_can_reorder(self):
        outsider = Teacher.objects.create(full_name="Other",pin_hash="!",is_active=True)
        other_session = Session.objects.create(title="Other",author=outsider)
        other_theory = TheoryMaterialModule.objects.create(session=other_session,position=1,title="Other")
        other_quiz = TheoryQuizModule.objects.create(session=other_session,position=2,title="Other quiz")
        for url in (f"/api/teacher/sessions/{other_session.id}/modules/reorder/",
                    f"/api/teacher/theory-modules/{other_theory.id}/blocks/reorder/",
                    f"/api/teacher/theory-quizzes/{other_quiz.id}/questions/reorder/"):
            self.assertEqual(self.request_json("post",url,{"items":[]}).status_code,404)
            self.assertEqual(Client().post(url,"{}",content_type="application/json").status_code,401)

    def test_coding_editor_round_trip_and_position_conflict(self):
        url = f"/api/teacher/tasks/{self.task.id}/"
        response = self.request_json("patch",url,{"title":"Updated","statement":"print a sum","constraints":"a < 10","programming_language":"cpp","hints_enabled":True,"hint1_unlock_attempts":4})
        self.assertEqual(response.status_code,200)
        task = self.client.get(url).json()["task"]
        self.assertEqual(task["statement"],"print a sum")
        self.assertEqual(task["programming_language"],"cpp")
        self.assertTrue(task["hints_enabled"])
        self.assertEqual(task["hint1_unlock_attempts"],4)
        self.assertEqual(self.request_json("patch",url,{"position":self.theory.position}).status_code,409)
        test = self.request_json("post",url+"tests/",{"ordinal":1,"stdin":"2\n","expected_stdout":"4\n","is_visible":False}).json()["test"]
        self.assertEqual(self.request_json("patch",f"/api/teacher/tests/{test['id']}/",{"expected_stdout":"5\n","is_visible":True}).status_code,200)
        self.assertEqual(self.client.get(url+"tests/").json()["tests"][0]["expected_stdout"],"5\n")
        fragment = self.request_json("post",url+"fragments/",{"position":"top","title":"Import","code":"#include <iostream>","is_active":True}).json()["fragment"]
        self.assertEqual(self.request_json("patch",f"/api/teacher/fragments/{fragment['id']}/",{"code":"// updated","is_active":False}).status_code,200)
        self.assertEqual(self.client.get(url+"fragments/").json()["fragments"][0]["code"],"// updated")
        old_page = self.client.get(f"/teacher/tasks/?session_id={self.lesson.id}")
        self.assertRedirects(old_page,f"/teacher/modules/?session_id={self.lesson.id}",fetch_redirect_response=False)

    def test_edit_theory_and_each_quiz_type_after_reorder(self):
        block = TheoryMaterialBlock.objects.create(module=self.theory,ordinal=1,block_type="text",content="Before")
        self.assertEqual(self.request_json("patch",f"/api/teacher/theory-blocks/{block.id}/",{"block_type":"heading","heading_level":"h2","content":"Edited"}).status_code,200)
        block.refresh_from_db()
        self.assertEqual((block.block_type,block.heading_level,block.content),("heading","h2","Edited"))
        question = TheoryQuizQuestion.objects.create(module=self.quiz,ordinal=1,question_type="open_answer",prompt="Before",model_answer="old")
        url = f"/api/teacher/theory-quiz-questions/{question.id}/"
        for payload in (
            {"question_type":"single_choice","prompt":"Choose","choices":[{"text":"A","is_correct":False},{"text":"B","is_correct":True}]},
            {"question_type":"matching","prompt":"Match","pairs":[{"left_text":"1","right_text":"one"},{"left_text":"2","right_text":"two"}]},
            {"question_type":"open_answer","prompt":"Explain","model_answer":"New answer","accept_suitable_answer":True},
        ):
            with self.subTest(kind=payload["question_type"]):
                res=self.request_json("patch",url,payload)
                self.assertEqual(res.status_code,200)
                self.assertEqual(res.json()["question"]["prompt"],payload["prompt"])
        question.refresh_from_db()
        self.assertEqual(question.model_answer,"New answer")
        self.assertTrue(question.accept_suitable_answer)
        self.assertFalse(TheoryQuizChoice.objects.filter(question=question).exists())
        self.assertFalse(TheoryQuizMatchPair.objects.filter(question=question).exists())
