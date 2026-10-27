import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (Exam, ExamAnswer, ExamAttempt, ExamQuestion, GameModule, Session, SessionTask, StudentClassMembership,
    StudentSession, StudentTaskProgress, StudentTheoryQuizAttempt, Submission, TheoryMaterialBlock, TheoryMaterialModule, TheoryQuizModule)
from tower.companion_contracts import SourceKind
from tower.models import CompanionLearningVisit, CompanionMessage, TowerPlayer, WalletEntry
from tower.services.companion_delivery import (SourceReservationError, acknowledge_learning_reply,
    reserve_learning_source, store_learning_reply)
from tower.services.companion_sources import MAX_COMMENT, MAX_TEXT, record_learning_visit, select_learning_source
from .wallet_fixtures import WalletFixtures


class SourceFixtures(WalletFixtures):
    def lesson(self, title="Lesson", *, group=None, visited=True, at=None, status="running"):
        now = timezone.now()
        row = Session.objects.create(title=title, author=(group or self.group).owner, status=status,
            starts_at=now - timedelta(hours=3), ends_at=now + timedelta(hours=6))
        row.allowed_classes.add(group or self.group)
        if visited:
            StudentSession.objects.create(student=self.student, session=row,
                started_at=at or now - timedelta(hours=1), last_seen_at=at or now - timedelta(hours=1))
        return row

    def task(self, lesson=None, *, position=1, solved=False):
        lesson = lesson or self.lesson()
        task = SessionTask.objects.create(session=lesson, position=position, title=f"Code {position}", statement="Read two integers and output their sum.")
        if solved:
            ss = StudentSession.objects.get(student=self.student, session=lesson)
            StudentTaskProgress.objects.create(student_session=ss, task=task, status="solved", solved_at=timezone.now())
        return task

    def work(self):
        lesson = self.lesson()
        self.task(lesson, position=1, solved=True)
        return self.task(lesson, position=2)

    def feedback(self, *, student=None, comment="Check the boundary of the loop.", score=0, status="submitted"):
        student = student or self.student
        exam = Exam.objects.create(owner=self.teacher, title="Assessment", status="stopped")
        exam.allowed_classes.add(student.class_group)
        question = ExamQuestion.objects.create(exam=exam, position=1, question_type="open_text", prompt="Describe the loop.", model_answer="HIDDEN MODEL SOLUTION", max_score=2)
        now = timezone.now()
        attempt = ExamAttempt.objects.create(exam=exam, student=student, status=status,
            started_at=now - timedelta(hours=1), expires_at=now + timedelta(hours=1))
        return ExamAnswer.objects.create(attempt=attempt, question=question, text_answer="PRIVATE STUDENT ANSWER",
            awarded_score=score, teacher_feedback=comment)

    def fact(self, lesson=None, *, content="A variable stores a value.", ordinal=1, module=None):
        module = module or TheoryMaterialModule.objects.create(session=lesson or self.lesson(), position=1, title="Variables")
        return TheoryMaterialBlock.objects.create(module=module, ordinal=ordinal, block_type="text", content=content)

    def deliver(self, text="Review the source before continuing."):
        message = reserve_learning_source(student=self.student)
        store_learning_reply(student=self.student, message_id=message.pk, text=text)
        return acknowledge_learning_reply(student=self.student, message_id=message.pk)


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CompanionSourceTests(SourceFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()

    def source(self, **kwargs):
        return select_learning_source(student=self.student, **kwargs)

    def test_all_three_priorities_and_completed_code(self):
        task = self.work()
        answer = self.feedback()
        block = self.fact(lesson=task.session)
        self.assertEqual(self.source().identity[:3], (SourceKind.UNFINISHED, "coding_task", task.pk))
        ss = StudentSession.objects.get(student=self.student, session=task.session)
        StudentTaskProgress.objects.create(student_session=ss, task=task, status="solved")
        self.assertEqual(self.source().resource_id, answer.pk)
        self.deliver()
        self.assertEqual((self.source().kind, self.source().resource_id), (SourceKind.LESSON_FACT, block.pk))

    def test_current_uncompleted_work_is_not_called_forgotten(self):
        self.task()
        self.assertIsNone(self.source())

    def test_accepted_submission_is_completed_even_when_progress_flag_is_stale(self):
        task=self.work()
        ss=StudentSession.objects.get(student=self.student,session=task.session)
        progress=StudentTaskProgress.objects.create(student_session=ss,task=task,status="in_progress")
        Submission.objects.create(progress=progress,attempt_no=1,code="PRIVATE CODE",verdict="accepted")
        self.assertIsNone(self.source())

    def test_moving_to_another_visited_lesson_exposes_old_unfinished_work(self):
        task = self.task(self.lesson(at=timezone.now()-timedelta(hours=2)))
        self.lesson("Next", at=timezone.now())
        self.assertEqual(self.source().resource_id, task.pk)

    def test_unvisited_future_closed_draft_expired_and_finished_lessons_are_not_actionable(self):
        for values in ({"visited": False}, {"status": "closed"}, {"status": "draft"}):
            lesson = self.lesson(**values)
            self.task(lesson)
        expired = self.lesson()
        expired.ends_at = timezone.now() - timedelta(seconds=1)
        expired.save()
        self.task(expired)
        finished = self.lesson()
        StudentSession.objects.filter(session=finished).update(finished_at=timezone.now())
        self.task(finished)
        self.assertIsNone(self.source())

    def test_any_submitted_quiz_counts_as_completed_even_with_zero_score(self):
        lesson = self.lesson()
        quiz = TheoryQuizModule.objects.create(session=lesson, position=1, title="Quiz")
        self.task(lesson, position=2, solved=True)
        self.assertEqual((self.source().resource_type, self.source().resource_id), ("theory_quiz", quiz.pk))
        StudentTheoryQuizAttempt.objects.create(student_session=StudentSession.objects.get(session=lesson), module=quiz,
            attempt_no=1, score_percent=0, total_questions=5)
        self.assertIsNone(self.source())

    def test_disabled_quiz_is_not_a_missing_assignment(self):
        lesson = self.lesson()
        self.task(lesson, solved=True)
        TheoryQuizModule.objects.create(session=lesson, position=2, title="Hidden quiz", is_active=False)
        self.assertIsNone(self.source())

    def test_exam_statuses_and_deadline_without_mutating_attempts(self):
        now = timezone.now()
        exam = Exam.objects.create(owner=self.teacher, title="Assigned", status="running")
        exam.allowed_classes.add(self.group)
        self.assertEqual((self.source().resource_type, self.source().context["status"]), ("exam", "not_started"))
        attempt = ExamAttempt.objects.create(exam=exam, student=self.student, status="in_progress", started_at=now, expires_at=now+timedelta(minutes=20))
        self.assertEqual(self.source().context["status"], "in_progress")
        for status in ("submitted", "expired"):
            attempt.status=status
            attempt.save()
            self.assertIsNone(self.source())
        attempt.status="in_progress"
        attempt.expires_at=now-timedelta(seconds=1)
        attempt.save()
        self.assertIsNone(self.source())
        attempt.refresh_from_db()
        self.assertEqual(attempt.status,"in_progress")

    def test_exam_requires_current_assignment_and_running_status(self):
        exam = Exam.objects.create(owner=self.teacher, title="Private", status="running")
        exam.allowed_classes.add(self.other_group)
        self.assertIsNone(self.source())
        exam.allowed_classes.add(self.group)
        self.assertEqual(self.source().resource_id, exam.pk)
        for status in ("stopped", "draft"):
            exam.status=status
            exam.save()
            self.assertIsNone(self.source())

    def test_multiple_classes_include_primary_even_when_extra_membership_exists(self):
        StudentClassMembership.objects.create(student=self.student, class_group=self.other_group)
        task = self.work()
        source = self.source()
        self.assertEqual(source.resource_id, task.pk)
        self.assertEqual(self.account_client().get(source.url).status_code, 200)
        self.assertEqual(self.account_client().get(reverse("student_task_detail", args=[task.pk])).status_code, 200)
        StudentTaskProgress.objects.update_or_create(student_session=StudentSession.objects.get(session=task.session), task=task, defaults={"status":"solved"})
        other = self.lesson("Extra class", group=self.other_group)
        self.task(other, solved=True)
        task2 = self.task(other, position=2)
        self.assertEqual(self.source().resource_id, task2.pk)
        StudentClassMembership.objects.filter(student=self.student).delete()
        self.assertIsNone(self.source())

    def test_whitespace_ungraded_full_score_and_inprogress_feedback_are_excluded(self):
        for params in ({"comment":" \n  "}, {"score":None}, {"score":2}, {"status":"in_progress"}):
            self.feedback(**params)
        self.assertIsNone(self.source())

    def test_feedback_is_own_graded_work_and_does_not_expose_answers(self):
        self.feedback(student=self.other_student)
        own = self.feedback()
        result = self.source().as_dict()
        self.assertEqual(result["resource_id"],own.pk)
        text = json.dumps(result)
        self.assertNotIn("HIDDEN MODEL SOLUTION",text)
        self.assertNotIn("PRIVATE STUDENT ANSWER",text)
        self.assertIn(f"#question-{own.question_id}", result["url"])
        self.assertEqual(self.account_client().get(result["url"]).status_code,200)

    def test_feedback_remains_available_in_own_result_after_assignment_removed(self):
        answer=self.feedback()
        answer.attempt.exam.allowed_classes.clear()
        self.assertEqual(self.source().resource_id,answer.pk)
        self.assertEqual(self.account_client().get(self.source().url).status_code,200)

    def test_bounded_context_and_full_comment_version(self):
        answer=self.feedback(comment="A"*10000)
        source=self.source()
        self.assertEqual(len(source.context["comment"]),MAX_COMMENT)
        self.deliver()
        answer.teacher_feedback="A"*9999+"B"
        answer.save()
        self.assertNotEqual(self.source().revision,source.revision)

    def test_facts_use_real_last_visit_not_creation_or_background_polling(self):
        older=self.lesson("Created first")
        wanted=self.fact(older)
        later=self.lesson("Created last",at=timezone.now()+timedelta(hours=1))
        self.fact(later,content="Other fact")
        record_learning_visit(student_id=self.student.pk,module=wanted.module,module_type="theory_material")
        self.assertEqual(self.source().resource_id,wanted.pk)
        # A polling attendance update does not supersede an actual module visit.
        StudentSession.objects.filter(session=later).update(last_seen_at=timezone.now()+timedelta(hours=2))
        self.assertEqual(self.source().resource_id,wanted.pk)

    def test_latest_lesson_without_text_returns_empty_instead_of_older_material(self):
        self.fact()
        latest=self.lesson("No text",at=timezone.now())
        module=TheoryMaterialModule.objects.create(session=latest,position=1,title="Empty")
        for ordinal, kind, content in ((1,"text"," \n "),(2,"heading","Only a heading"),(3,"code","print(1)")):
            TheoryMaterialBlock.objects.create(module=module,ordinal=ordinal,block_type=kind,content=content)
        self.assertIsNone(self.source())

    def test_fact_module_and_block_randomization_and_no_consecutive_repeat(self):
        lesson=self.lesson()
        first=self.fact(lesson,content="First")
        second=self.fact(lesson,module=first.module,ordinal=2,content="Second")
        third_module=TheoryMaterialModule.objects.create(session=lesson,position=2,title="Another module")
        third=self.fact(lesson,module=third_module,content="Third")
        choices=[]
        def choose(values):
            choices.append(values)
            return values[-1]
        source=self.source(chooser=choose)
        self.assertEqual(source.resource_id,third.pk)
        self.assertEqual(len(choices),2)
        self.deliver()
        # Whichever fact was randomly displayed cannot be immediately selected again.
        shown=CompanionMessage.objects.get(delivered_at__isnull=False)
        for _ in range(5):
            self.assertNotEqual(self.source().resource_id,shown.resource_id)

    def test_only_one_available_fact_can_repeat_and_long_text_is_bounded(self):
        block=self.fact(content="Text "*5000)
        self.assertEqual(len(self.source().context["text"]),MAX_TEXT)
        self.deliver()
        self.assertEqual(self.source().resource_id,block.pk)
        block.module.is_active=False
        block.module.save()
        self.assertIsNone(self.source())

    def test_readiness_endpoint_is_private_read_only_and_does_not_call_ai(self):
        task=self.work()
        before=CompanionMessage.objects.count()
        with patch("urllib.request.urlopen",side_effect=AssertionError("No network allowed")):
            response=self.account_client().get(reverse("tower:companion_source"),{"student_id":self.other_student.pk})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["source"]["resource_id"],task.pk)
        self.assertIn("no-store",response.headers["Cache-Control"])
        self.assertEqual(CompanionMessage.objects.count(),before)
        self.assertEqual(WalletEntry.objects.count(),0)
        self.assertEqual(Client().get(reverse("tower:companion_source")).status_code,401)
        self.assertIsNone(self.account_client(self.other_student).get(reverse("tower:companion_source")).json()["source"])
        self.assertEqual(self.account_client().post(reverse("tower:companion_source")).status_code,405)

    def test_revoked_guest_and_inactive_accounts_cannot_select_sources(self):
        self.work()
        for field,value in (("is_active",False),("guest_lease_token",uuid.uuid4())):
            original=getattr(self.student,field)
            setattr(self.student,field,value)
            self.student.save()
            with self.assertRaises(PermissionDenied): self.source()
            setattr(self.student,field,original)
            self.student.save()
        with override_settings(TOWER_ENABLED=False):
            with self.assertRaises(PermissionDenied): self.source()

    def test_real_detail_endpoints_record_visits_but_readiness_and_poll_do_not(self):
        lesson=self.lesson()
        task=self.task(lesson)
        module=self.fact(lesson).module
        quiz=TheoryQuizModule.objects.create(session=lesson,position=3,title="Quiz")
        client=self.account_client()
        for name,resource,kind in (("student_task_detail",task,"coding_task"),("student_theory_module_detail",module,"theory_material"),("student_theory_quiz_detail",quiz,"theory_quiz")):
            self.assertEqual(client.get(reverse(name,args=[resource.pk])).status_code,200)
            self.assertTrue(CompanionLearningVisit.objects.filter(student=self.student,resource_id=resource.pk,module_type=kind).exists())
        before=list(CompanionLearningVisit.objects.values_list("pk","first_visited_at","last_visited_at"))
        client.get(reverse("student_active_session"))
        client.get(reverse("tower:companion_source"))
        self.assertEqual(list(CompanionLearningVisit.objects.values_list("pk","first_visited_at","last_visited_at")),before)

    def test_repeated_opens_keep_first_visit_and_update_last_visit(self):
        module=self.fact().module
        record_learning_visit(student_id=self.student.pk,module=module,module_type="theory_material")
        row=CompanionLearningVisit.objects.get()
        initial=row.first_visited_at
        record_learning_visit(student_id=self.student.pk,module=module,module_type="theory_material")
        row.refresh_from_db()
        self.assertEqual(row.first_visited_at,initial)
        self.assertGreaterEqual(row.last_visited_at,initial)

    def test_game_visit_keeps_own_lesson_material_accessible_after_closing(self):
        lesson=self.lesson(visited=False)
        game=GameModule.objects.create(session=lesson,position=1,title="Game")
        block=self.fact(lesson)
        client=self.account_client()
        self.assertEqual(client.get(reverse("student_game_module_api",args=[game.pk])).status_code,200)
        self.assertTrue(StudentSession.objects.filter(student=self.student,session=lesson).exists())
        lesson.status="closed"
        lesson.save()
        source=self.source()
        self.assertEqual(source.resource_id,block.pk)
        self.assertEqual(client.get(source.url).status_code,200)
        self.assertEqual(client.get(reverse("student_theory_module_detail",args=[block.module_id])).status_code,200)

    def test_foreign_modules_and_revoked_session_access_do_not_leak_or_record_visits(self):
        foreign=self.lesson(group=self.other_group)
        module=self.fact(foreign).module
        client=self.account_client()
        self.assertEqual(client.get(reverse("student_theory_module_detail",args=[module.pk])).status_code,403)
        record_learning_visit(student_id=self.student.pk,module=module,module_type="theory_material")
        self.assertFalse(CompanionLearningVisit.objects.exists())
        self.assertIsNone(self.source())
        with self.assertRaises(ValueError):
            record_learning_visit(student_id=self.student.pk,module=module,module_type="coding_task")

    def test_deep_link_disambiguates_code_and_theory_with_same_numeric_id(self):
        lesson=self.lesson()
        task=self.task(lesson,solved=True)
        module=TheoryMaterialModule.objects.create(pk=task.pk,session=lesson,position=2,title="Theory with same ID")
        block=self.fact(lesson,module=module)
        source=self.source()
        self.assertEqual(source.resource_id,block.pk)
        self.assertIn("module_type=theory_material",source.url)
        self.assertIn(f"#theory-block-{block.pk}",source.url)
        self.assertEqual(self.account_client().get(source.url).status_code,200)


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CompanionDeliveryTests(SourceFixtures, TestCase):
    @classmethod
    def setUpTestData(cls): cls.create_accounts()

    def test_feedback_consumed_only_after_saved_reply_is_acknowledged(self):
        answer=self.feedback()
        message=reserve_learning_source(student=self.student)
        self.assertIsNotNone(select_learning_source(student=self.student))
        with self.assertRaises(SourceReservationError):
            acknowledge_learning_reply(student=self.student,message_id=message.pk)
        store_learning_reply(student=self.student,message_id=message.pk,text="Review the loop boundary.")
        # Network loss: next request returns the same reply and ID, even after the reservation timeout.
        same=reserve_learning_source(student=self.student,now=timezone.now()+timedelta(minutes=5))
        self.assertEqual((same.pk,same.text),(message.pk,"Review the loop boundary."))
        self.assertIsNotNone(select_learning_source(student=self.student))
        acknowledge_learning_reply(student=self.student,message_id=message.pk)
        acknowledge_learning_reply(student=self.student,message_id=message.pk)
        self.assertIsNone(select_learning_source(student=self.student))
        self.assertEqual(CompanionMessage.objects.count(),1)

    def test_comment_version_and_character_switch_do_not_reset_consumption(self):
        answer=self.feedback()
        self.deliver()
        player=TowerPlayer.objects.create(student=self.student,appearance={"companion":"nis-coder-companion"})
        player.appearance={"companion":"nis-artist-companion"}
        player.save()
        answer.teacher_feedback="  Check the boundary\nof the loop.  "
        answer.save()
        self.assertIsNone(select_learning_source(student=self.student))
        answer.teacher_feedback="Review the termination condition instead."
        answer.save()
        self.assertEqual(select_learning_source(student=self.student).resource_id,answer.pk)

    def test_expired_reservation_and_late_response_do_not_overwrite_new_operation(self):
        self.feedback()
        first=reserve_learning_source(student=self.student)
        later=first.reserved_until+timedelta(seconds=1)
        replacement=reserve_learning_source(student=self.student,now=later)
        self.assertNotEqual(first.pk,replacement.pk)
        with self.assertRaises(SourceReservationError):
            store_learning_reply(student=self.student,message_id=first.pk,text="Late response.",now=later)
        store_learning_reply(student=self.student,message_id=replacement.pk,text="Current response.",now=later)
        self.assertEqual(CompanionMessage.objects.filter(completed_at__isnull=False).count(),1)

    def test_permissions_and_progress_are_rechecked_before_saving_or_acknowledging(self):
        task=self.work()
        message=reserve_learning_source(student=self.student)
        ss=StudentSession.objects.get(session=task.session)
        StudentTaskProgress.objects.create(student_session=ss,task=task,status="solved")
        with self.assertRaises(SourceReservationError):
            store_learning_reply(student=self.student,message_id=message.pk,text="Outdated.")
        answer=self.feedback()
        second=reserve_learning_source(student=self.student)
        store_learning_reply(student=self.student,message_id=second.pk,text="Read the comment.")
        answer.teacher_feedback="A materially different comment."
        answer.save()
        with self.assertRaises(SourceReservationError):
            acknowledge_learning_reply(student=self.student,message_id=second.pk)
        self.assertFalse(CompanionMessage.objects.filter(delivered_at__isnull=False).exists())

    def test_other_account_invalid_reply_and_conflicting_saved_result_are_rejected(self):
        self.feedback()
        message=reserve_learning_source(student=self.student)
        with self.assertRaises(SourceReservationError):
            store_learning_reply(student=self.other_student,message_id=message.pk,text="Other student.")
        for text in ("", " \n ", "A"*601, None):
            with self.assertRaises(SourceReservationError):
                store_learning_reply(student=self.student,message_id=message.pk,text=text)
        store_learning_reply(student=self.student,message_id=message.pk,text="Saved reply.")
        store_learning_reply(student=self.student,message_id=message.pk,text="Saved reply.")
        with self.assertRaises(SourceReservationError):
            store_learning_reply(student=self.student,message_id=message.pk,text="Changed reply.")

    def test_revoked_permissions_invalidate_an_existing_reservation(self):
        task=self.work()
        message=reserve_learning_source(student=self.student)
        task.session.allowed_classes.clear()
        with self.assertRaises(SourceReservationError):
            store_learning_reply(student=self.student,message_id=message.pk,text="Now inaccessible.")
        self.assertIsNone(reserve_learning_source(student=self.student))
        self.assertFalse(CompanionMessage.objects.filter(delivered_at__isnull=False).exists())

    def test_empty_selection_does_not_reserve_or_create_an_account(self):
        self.assertIsNone(reserve_learning_source(student=self.student))
        self.assertFalse(CompanionMessage.objects.exists())
        self.assertFalse(TowerPlayer.objects.exists())

    def test_unfinished_sources_rotate_without_falling_to_lower_priority(self):
        first=self.work()
        second=self.task(first.session,position=3)
        self.feedback()
        shown=self.deliver()
        source=select_learning_source(student=self.student)
        self.assertEqual(source.kind,SourceKind.UNFINISHED)
        self.assertNotEqual(source.resource_id,shown.resource_id)
        self.assertIn(source.resource_id,(first.pk,second.pk))


@skipUnless(connection.vendor=="postgresql","Source reservation races require PostgreSQL row locks")
@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CompanionSourceConcurrencyTests(SourceFixtures, TransactionTestCase):
    def setUp(self):
        self.create_accounts()
        self.feedback()

    def test_parallel_reservations_share_one_message(self):
        def reserve(_):
            close_old_connections()
            try: return reserve_learning_source(student=self.student).pk
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(reserve,range(2)))
        self.assertEqual(ids[0],ids[1])
        self.assertEqual(CompanionMessage.objects.count(),1)
