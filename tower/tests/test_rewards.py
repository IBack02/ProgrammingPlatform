import json
import uuid
from unittest.mock import patch
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    GameModule, GameParticipant, GameRound, Session, SessionClass, SessionTask,
    Student, StudentClassMembership, TheoryMaterialModule,
    TheoryQuizModule, TheoryQuizQuestion, TheoryQuizChoice, TaskTestCase,
)
from tower.models import MAX_BALANCE, RewardBatch, RewardPolicy, TowerAuditEvent, Wallet, WalletEntry
from tower.services.rewards import award_batch, learning_reward, save_policy
from tower.services.wallet import WalletError, student_wallet
from .wallet_fixtures import WalletFixtures


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RewardTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()
        cls.session_obj = Session.objects.create(title="Lesson", author=cls.teacher, status=Session.Status.RUNNING)
        SessionClass.objects.create(session=cls.session_obj, class_group=cls.group)
        cls.page = TheoryMaterialModule.objects.create(session=cls.session_obj, position=1, title="Theory")
        cls.task = SessionTask.objects.create(session=cls.session_obj, position=2, title="Code", statement="Print 1")
        TaskTestCase.objects.create(task=cls.task, ordinal=1, stdin="", expected_stdout="1")
        cls.quiz = TheoryQuizModule.objects.create(session=cls.session_obj, position=3, title="Quiz")
        question = TheoryQuizQuestion.objects.create(module=cls.quiz, ordinal=1, question_type="single_choice", prompt="1+1?")
        cls.choice = TheoryQuizChoice.objects.create(question=question, ordinal=1, text="2", is_correct=True)
        cls.game = GameModule.objects.create(session=cls.session_obj, position=4, title="Race", rubric=GameModule.Rubric.MIND_RACE)
        cls.policy = RewardPolicy.objects.create(class_group=cls.group, race_first=100, race_second=60, race_third=30, task_solved=15, page_opened=3)

    def batch(self, **kwargs):
        values = dict(class_id=self.group.pk, student_ids=[self.student.pk], amount=10, reason="Participation", request_id=uuid.uuid4(), teacher=self.teacher)
        values.update(kwargs)
        return award_batch(**values)

    def event(self, **kwargs):
        values = dict(student_id=self.student.pk, class_id=self.group.pk, session_id=self.session_obj.pk, event="page", resource_id=self.page.pk, title=self.page.title)
        values.update(kwargs)
        return learning_reward(**values)

    def test_batch_awards_each_selected_student_once_and_detects_changed_payload(self):
        peer = Student.objects.create(full_name="Peer", class_group=self.group, pin_hash="unused")
        key = uuid.uuid4()
        self.batch(student_ids=[self.student.pk, peer.pk], request_id=key)
        self.assertTrue(self.batch(student_ids=[peer.pk, self.student.pk], request_id=key)["replayed"])
        self.assertEqual(list(Wallet.objects.values_list("niscoin", flat=True)), [10, 10])
        with self.assertRaises(WalletError):
            self.batch(student_ids=[self.student.pk], request_id=key)
        self.assertEqual(RewardBatch.objects.count(), 1)

    def test_batch_rolls_back_every_recipient_on_failure(self):
        peer = Student.objects.create(full_name="Peer", class_group=self.group, pin_hash="unused")
        wallet = student_wallet(peer)
        Wallet.objects.filter(pk=wallet.pk).update(niscoin=MAX_BALANCE)
        with self.assertRaises(WalletError):
            self.batch(student_ids=[self.student.pk, peer.pk])
        self.assertFalse(WalletEntry.objects.exists())
        self.assertFalse(RewardBatch.objects.exists())

    def test_batch_rejects_outsiders_and_revoked_permissions(self):
        with self.assertRaises(Http404):
            self.batch(student_ids=[self.student.pk, self.other_student.pk])
        self.assertFalse(Wallet.objects.exists())
        self.teacher.can_manage_tower = False
        self.teacher.save(update_fields=["can_manage_tower"])
        with self.assertRaises(PermissionDenied):
            self.batch()

    def test_rules_validate_podium_and_record_audit(self):
        values = dict(race_first=100, race_second=50, race_third=20, task_solved=10, page_opened=0)
        save_policy(class_id=self.group.pk, teacher=self.teacher, values=values)
        self.assertEqual(RewardPolicy.objects.get().page_opened, 0)
        self.assertEqual(TowerAuditEvent.objects.get(event_type="reward_policy").metadata["after"], values)
        for invalid in [dict(values, race_second=101), dict(values, page_opened=-1), dict(values, task_solved=True), dict(values, race_first=10001)]:
            with self.assertRaises(WalletError):
                save_policy(class_id=self.group.pk, teacher=self.teacher, values=invalid)

    def test_policy_save_rolls_back_if_audit_fails(self):
        with patch("tower.services.rewards.TowerAuditEvent.objects.create", side_effect=RuntimeError("audit")):
            with self.assertRaises(RuntimeError):
                save_policy(class_id=self.group.pk, teacher=self.teacher, values=dict(
                    race_first=200, race_second=0, race_third=0, task_solved=0, page_opened=0))
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.race_first, 100)

    def test_reward_once_even_when_amount_or_class_changes(self):
        self.event()
        RewardPolicy.objects.filter(pk=self.policy.pk).update(page_opened=99)
        self.assertIsNone(self.event())
        StudentClassMembership.objects.create(student=self.student, class_group=self.other_group)
        SessionClass.objects.create(session=self.session_obj, class_group=self.other_group)
        RewardPolicy.objects.create(class_group=self.other_group, page_opened=50)
        self.assertIsNone(self.event(class_id=self.other_group.pk))
        self.assertEqual(Wallet.objects.get().niscoin, 3)
        self.assertEqual(WalletEntry.objects.get().source, "learning_reward")

    def test_disabled_rules_permissions_and_unassigned_session_do_not_award(self):
        with override_settings(TOWER_ENABLED=False):
            self.assertIsNone(self.event())
        self.assertIsNone(self.event(class_id=self.other_group.pk))
        self.assertIsNone(self.event(session_id=99999))
        RewardPolicy.objects.filter(pk=self.policy.pk).update(page_opened=0)
        self.assertIsNone(self.event())
        self.assertFalse(Wallet.objects.exists())

    def test_page_endpoint_awards_once_and_denies_other_class(self):
        url = reverse("student_theory_module_detail", args=[self.page.pk])
        client = self.account_client()
        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(Wallet.objects.get().niscoin, 3)
        self.assertEqual(self.account_client(self.other_student).get(url).status_code, 403)

    def test_quiz_endpoint_rewards_only_perfect_result_once(self):
        client = self.account_client()
        url = reverse("student_theory_quiz_submit", args=[self.quiz.pk])
        self.assertEqual(client.post(url, json.dumps({"answers": {}}), content_type="application/json").status_code, 200)
        self.assertFalse(Wallet.objects.exists())
        payload = json.dumps({"answers": {str(self.choice.question_id): self.choice.pk}})
        for _ in range(2):
            self.assertEqual(client.post(url, payload, content_type="application/json").status_code, 200)
        self.assertEqual(Wallet.objects.get().niscoin, 15)

    @patch("core.views.wait_batch")
    @patch("core.views.create_batch_submissions", return_value=["test-token"])
    def test_programming_endpoint_rewards_only_accepted_solution_once(self, create, wait):
        client = self.account_client()
        url = reverse("student_submit", args=[self.task.pk])
        wait.return_value = [SimpleNamespace(token="test-token", status_id=3, stdout="1", stderr="")]
        payload = json.dumps({"code": "print(1)"})
        response = client.post(url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["submission"]["verdict"], "accepted")
        self.assertEqual(Wallet.objects.get().niscoin, 15)
        self.assertEqual(client.post(url, payload, content_type="application/json").status_code, 200)
        self.assertEqual(WalletEntry.objects.count(), 1)

    @patch("core.views.wait_batch", return_value=[SimpleNamespace(token="test-token", status_id=4, stdout="0", stderr="")])
    @patch("core.views.create_batch_submissions", return_value=["test-token"])
    def test_incorrect_programming_solution_does_not_award(self, create, wait):
        response = self.account_client().post(reverse("student_submit", args=[self.task.pk]),
            json.dumps({"code": "print(0)"}), content_type="application/json")
        self.assertEqual(response.json()["submission"]["verdict"], "wrong_answer")
        self.assertFalse(Wallet.objects.exists())

    def test_race_pays_finishers_on_teacher_finish_and_never_unfinished_players(self):
        peer = Student.objects.create(full_name="Unfinished", class_group=self.group, pin_hash="unused")
        round_obj = GameRound.objects.create(module=self.game, class_group=self.group, moderator=self.teacher, run_number=1, status="running", total_prompts=2)
        GameParticipant.objects.create(round=round_obj, student=self.student, progress=2, finish_place=1)
        GameParticipant.objects.create(round=round_obj, student=peer, progress=1)
        self.assertFalse(Wallet.objects.exists())
        client = self.account_client(self.teacher)
        url = reverse("teacher_game_finish_round_api", args=[round_obj.pk])
        for _ in range(2):
            self.assertEqual(client.post(url).status_code, 200)
        self.assertEqual(Wallet.objects.get().niscoin, 100)
        self.assertEqual(WalletEntry.objects.count(), 1)

    def test_policy_and_batch_forms_work_from_journal(self):
        client = self.account_client(self.teacher)
        page = client.get(reverse("tower:teacher"))
        self.assertContains(page, 'id="automatic-rewards"')
        response = client.post(reverse("tower:teacher_wallets"), {"action": "policy", "class_id": self.group.pk, "race_first": 120, "race_second": 60, "race_third": 30, "task_solved": 20, "page_opened": 5})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RewardPolicy.objects.get().race_first, 120)
        payload = {"action": "batch", "class_id": self.group.pk, "student_ids": [self.student.pk], "amount": 10, "reason": "Great work", "request_id": uuid.uuid4()}
        for _ in range(2):
            self.assertEqual(client.post(reverse("tower:teacher_wallets"), payload).status_code, 302)
        self.assertEqual(Wallet.objects.get().niscoin, 10)
        payload["student_ids"] = []
        self.assertEqual(client.post(reverse("tower:teacher_wallets"), payload).status_code, 400)

    def test_learning_awards_visible_in_class_and_student_history(self):
        self.event()
        teacher = self.account_client(self.teacher)
        self.assertContains(teacher.get(reverse("tower:teacher")), self.page.title)
        self.assertEqual(teacher.get(reverse("tower:teacher_history", args=[self.student.pk]), {"class_id": self.group.pk}).json()["entries"][0]["source"], "learning_reward")
        self.assertEqual(self.account_client().get(reverse("tower:wallet")).json()["wallet"]["niscoin"], 3)
