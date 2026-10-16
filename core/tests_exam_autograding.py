import json
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.utils import timezone

from .exam_views import _create_question, _expire_if_needed
from .models import ClassGroup, Exam, ExamAnswer, ExamAttempt, ExamClass, Student, Teacher
from .security import auth_version


class ExamAutogradingTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(full_name="Test teacher", pin_hash="!")
        self.group = ClassGroup.objects.create(name="Test class", owner=self.teacher)
        self.student = Student.objects.create(full_name="Test student", class_group=self.group, pin_hash="!")
        self.teacher_client = self.auth_client(teacher=self.teacher)
        self.client = self.auth_client(student=self.student)
        self.exam = Exam.objects.create(owner=self.teacher, title="Autograde", auto_grade=True, status="running")
        ExamClass.objects.create(exam=self.exam, class_group=self.group)
        self.choice = _create_question(self.exam, {"position":1,"question_type":"single_choice","prompt":"Pick the answer","max_score":3,"choices":[{"text":"First","is_correct":False},{"text":"Second","is_correct":True}]})
        self.matching = _create_question(self.exam, {"position":2,"question_type":"matching","prompt":"Match","model_answer":"Pairs","max_score":5,"pairs":[{"left_text":"A","right_text":"1"},{"left_text":"B","right_text":"2"},{"left_text":"C","right_text":"3"}]})
        self.manual = _create_question(self.exam, {"position":3,"question_type":"open_text","prompt":"Explain","model_answer":"Explanation","max_score":2})
        self.base = f"/api/student/exams/{self.exam.id}"

    def auth_client(self, student=None, teacher=None):
        client=Client()
        session=client.session
        if teacher:
            session.update({"teacher_id":teacher.id,"teacher_auth_version":auth_version(teacher.pin_hash)})
        else:
            session.update({"student_id":student.id,"student_class_id":student.class_group_id,"student_name":student.full_name,"student_auth_version":auth_version(student.pin_hash)})
        session.save()
        return client

    def post(self, path, data=None, client=None):
        return (client or self.client).post(path,data=json.dumps(data or {}),content_type="application/json")

    def start(self):
        self.assertEqual(self.post(self.base+"/start/").status_code,200)
        return ExamAttempt.objects.get(exam=self.exam,student=self.student)

    def test_choices_hidden_keys_validated_and_draft_ungraded(self):
        attempt=self.start()
        data=self.client.get(self.base+"/").json()
        self.assertEqual(data["questions"][0]["choices"],[{"key":"1","text":"First"},{"key":"2","text":"Second"}])
        self.assertNotIn('is_correct',json.dumps(data))
        self.assertNotIn('model_answer',json.dumps(data))
        self.assertEqual(self.post(self.base+f"/questions/{self.choice.id}/answer/",{"choice_answer":"99"}).status_code,400)
        response=self.post(self.base+f"/questions/{self.choice.id}/answer/",{"choice_answer":"2","awarded_score":99,"text_answer":"forged"})
        self.assertEqual(response.status_code,200)
        answer=attempt.answers.get(question=self.choice)
        self.assertEqual(answer.text_answer,"Second")
        self.assertIsNone(answer.awarded_score)
        self.assertEqual(self.client.get(self.base+"/").json()["questions"][0]["answer"]["choice_answer"],"2")

    def test_submit_grades_choice_and_partial_matching_without_touching_manual(self):
        attempt=self.start()
        correct=attempt.presentation_json[str(self.matching.id)]["correct"]
        response=self.post(self.base+"/submit/",{"answers":[
            {"question_id":self.choice.id,"choice_answer":"2"},
            {"question_id":self.matching.id,"matching_answer":dict(list(correct.items())[:1])},
            {"question_id":self.manual.id,"text_answer":"My explanation"},
        ]})
        self.assertEqual(response.status_code,200,response.content)
        attempt.refresh_from_db()
        self.assertEqual(attempt.total_score,Decimal("4.67"))
        self.assertEqual(attempt.answers.get(question=self.matching).awarded_score,Decimal("1.67"))
        self.assertIsNone(attempt.answers.get(question=self.manual).awarded_score)
        review=self.teacher_client.get(f"/api/teacher/exam-attempts/{attempt.id}/").json()
        self.assertEqual(review["answers"][0]["answer"]["text_answer"],"Second")
        self.assertEqual(review["answers"][0]["awarded_score"],3)
        self.assertEqual(self.post(self.base+"/submit/").status_code,409)

    def test_wrong_and_unanswered_receive_zero_and_off_mode_stays_manual(self):
        attempt=self.start()
        self.post(self.base+"/submit/",{"answers":[{"question_id":self.choice.id,"choice_answer":"1"}]})
        self.assertEqual(attempt.answers.get(question=self.choice).awarded_score,0)
        self.assertEqual(attempt.answers.get(question=self.matching).awarded_score,0)
        attempt.delete()
        self.exam.auto_grade=False
        self.exam.save()
        attempt=self.start()
        self.post(self.base+"/submit/",{"answers":[{"question_id":self.choice.id,"choice_answer":"2"}]})
        self.assertIsNone(attempt.answers.get(question=self.choice).awarded_score)
        self.assertFalse(attempt.answers.filter(question=self.matching).exists())

    def test_all_expiry_paths_grade_only_saved_drafts(self):
        for path, payload, method in [
            ("/",None,"get"),
            ("/submit/",{"answers":[{"question_id":self.choice.id,"choice_answer":"1"}]},"post"),
            (f"/questions/{self.choice.id}/answer/",{"choice_answer":"1"},"post"),
            ("/integrity/",{"events":[{"event_type":"tab_hidden","client_event_id":"test"}]},"post"),
        ]:
            with self.subTest(path=path):
                attempt=self.start()
                self.post(self.base+f"/questions/{self.choice.id}/answer/",{"choice_answer":"2"})
                ExamAttempt.objects.filter(id=attempt.id).update(expires_at=timezone.now()-timedelta(seconds=1))
                if method=="get":
                    self.client.get(self.base+path)
                else:
                    self.post(self.base+path,payload)
                attempt.refresh_from_db()
                self.assertEqual(attempt.status,"expired")
                self.assertEqual(attempt.total_score,3)
                attempt.delete()

    def test_teacher_review_closes_expired_attempt_and_preserves_override(self):
        attempt=self.start()
        ExamAttempt.objects.filter(id=attempt.id).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.teacher_client.get(f"/api/teacher/exam-attempts/{attempt.id}/")
        answer=attempt.answers.get(question=self.choice)
        response=self.teacher_client.patch(f"/api/teacher/exam-answers/{answer.id}/grade/",data=json.dumps({"awarded_score":1.5}),content_type="application/json")
        self.assertEqual(response.status_code,200)
        _expire_if_needed(attempt)  # Stale in-progress model instance must not re-grade.
        attempt.refresh_from_db()
        self.assertEqual(attempt.total_score,Decimal("1.50"))

    def test_validation_clone_import_and_locked_settings(self):
        bad={"position":4,"question_type":"single_choice","prompt":"Bad","choices":[{"text":"a","is_correct":True},{"text":"b","is_correct":True}]}
        response=self.post(f"/api/teacher/exams/{self.exam.id}/questions/",bad,self.teacher_client)
        self.assertEqual(response.status_code,400)
        self.exam.is_shared_template=True
        self.exam.save()
        other=Teacher.objects.create(full_name="Template user",pin_hash="!")
        response=self.post(f"/api/teacher/exams/{self.exam.id}/clone/",client=self.auth_client(teacher=other))
        self.assertEqual(response.status_code,201,response.content)
        copied=Exam.objects.get(id=response.json()["exam"]["id"])
        self.assertTrue(copied.auto_grade)
        self.assertEqual(copied.questions.get(position=1).choices,self.choice.choices)
        response=self.post("/api/teacher/exams/import-json/",{"action":"create_exam","exam":{"title":"Imported","auto_grade":True},"questions":[{"position":1,"question_type":"single_choice","prompt":"Imported test","choices":self.choice.choices}]},self.teacher_client)
        self.assertEqual(response.status_code,200,response.content)
        self.assertTrue(response.json()["exam"]["auto_grade"])
        self.start()
        response=self.teacher_client.patch(f"/api/teacher/exams/{self.exam.id}/",data=json.dumps({"auto_grade":False}),content_type="application/json")
        self.assertEqual(response.status_code,409)
