import io
import json
import uuid
from unittest.mock import patch

from django.contrib import admin as admin_site
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse

from core.models import ClassGroup, StudentClassMembership, Teacher
from tower.models import MAX_BALANCE, GameOperation, TowerAuditEvent, TowerPlayer, Wallet, WalletEntry
from tower.services.wallet import WalletError, adjust_wallet, spend_coins, student_wallet
from .wallet_fixtures import WalletFixtures


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WalletTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()

    def assert_wallet_error(self, code, callback, **kwargs):
        with self.assertRaises(WalletError) as caught:
            callback(**kwargs)
        self.assertEqual(caught.exception.code, code)

    def api_award(self, client=None, **kwargs):
        values = dict(student_id=self.student.pk, class_id=self.group.pk, amount=50,
                      reason="Coding achievement", request_id=str(uuid.uuid4()))
        values.update(kwargs)
        return (client or self.account_client(self.teacher)).post(reverse("tower:award"), json.dumps(values), content_type="application/json")

    def test_wallet_is_created_lazily_and_shared_across_classes(self):
        self.assertFalse(TowerPlayer.objects.exists())
        first = student_wallet(self.student)
        StudentClassMembership.objects.create(student=self.student, class_group=self.other_group)
        self.assertEqual(first.pk, student_wallet(self.student).pk)
        self.assertEqual((TowerPlayer.objects.count(), Wallet.objects.count()), (1, 1))
        self.assertEqual((first.niscoin, first.universal_shards), (0, 0))

    def test_award_records_balance_operation_ledger_and_audit(self):
        result = self.award(reason="  Quiz   prize  ")
        wallet = Wallet.objects.get()
        entry = WalletEntry.objects.get()
        event = TowerAuditEvent.objects.get(student=self.student)
        self.assertEqual(wallet.niscoin, 100)
        self.assertEqual((entry.delta, entry.balance_after, entry.reason), (100, 100, "Quiz prize"))
        self.assertEqual((event.actor_teacher_id, event.entry_id), (self.teacher.pk, entry.pk))
        self.assertEqual(event.metadata, {"currency": "niscoin", "before": 0, "after": 100, "delta": 100})
        self.assertEqual(GameOperation.objects.get().result["entry_id"], result["entry_id"])

    def test_exact_retry_returns_original_result_even_after_another_award(self):
        key = uuid.uuid4()
        first = self.award(request_id=key)
        self.award(amount=20)
        retry = self.award(request_id=key)
        self.assertEqual(retry, {**first, "replayed": True})
        self.assertEqual(Wallet.objects.get().niscoin, 120)
        self.assertEqual(WalletEntry.objects.count(), 2)

    def test_reusing_request_with_changed_amount_or_target_conflicts(self):
        key = uuid.uuid4()
        self.award(request_id=key)
        self.assert_wallet_error("request_conflict", self.award, request_id=key, amount=90)
        StudentClassMembership.objects.create(student=self.other_student, class_group=self.group)
        self.assert_wallet_error("request_conflict", self.award, request_id=key, student_id=self.other_student.pk)
        self.assertEqual(WalletEntry.objects.count(), 1)
        self.assertEqual(Wallet.objects.count(), 1)

    def test_same_uuid_for_different_teachers_is_independent(self):
        StudentClassMembership.objects.create(student=self.student, class_group=self.other_group)
        key = uuid.uuid4()
        self.award(request_id=key)
        self.award(request_id=key, teacher=self.other_teacher, class_id=self.other_group.pk)
        self.assertEqual(Wallet.objects.get().niscoin, 200)

    def test_invalid_amounts_rejected_without_side_effects(self):
        for amount in [0, -1, True, 1.5, "10", 10001, None]:
            with self.subTest(amount=amount):
                self.assert_wallet_error("invalid_amount", self.award, amount=amount)
        self.assertFalse(Wallet.objects.exists())
        self.assertFalse(GameOperation.objects.exists())

    def test_reason_and_uuid_required(self):
        for reason in ["", "   ", "a" * 241, 123, None]:
            self.assert_wallet_error("reason_required", self.award, reason=reason)
        self.assert_wallet_error("invalid_request_id", self.award, request_id="broken")
        self.assertFalse(WalletEntry.objects.exists())

    def test_teacher_cannot_award_other_class_or_outsider(self):
        with self.assertRaises(Http404):
            self.award(class_id=self.other_group.pk)
        with self.assertRaises(Http404):
            self.award(student_id=self.other_student.pk)

    def test_permissions_rechecked_after_revocation(self):
        self.award()
        Teacher.objects.filter(pk=self.teacher.pk).update(can_manage_tower=False)
        with self.assertRaises(PermissionDenied):
            self.award()
        with self.assertRaises(PermissionDenied):
            student_wallet(self.student)
        self.assertEqual(Wallet.objects.get().niscoin, 100)

    def test_disabled_then_reenabled_preserves_balance(self):
        self.award()
        ClassGroup.objects.filter(pk=self.group.pk).update(tower_enabled=False)
        self.assertEqual(self.account_client().get(reverse("tower:wallet")).status_code, 403)
        ClassGroup.objects.filter(pk=self.group.pk).update(tower_enabled=True)
        self.assertEqual(student_wallet(self.student).niscoin, 100)

    def test_global_switch_guests_and_inactive_students_cannot_create_wallet(self):
        with override_settings(TOWER_ENABLED=False), self.assertRaises(PermissionDenied):
            self.award()
        self.student.is_active = False
        self.student.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            student_wallet(self.student)
        self.student.is_active = True
        self.student.guest_lease_token = uuid.uuid4()
        self.student.save(update_fields=["is_active", "guest_lease_token"])
        with self.assertRaises(PermissionDenied):
            student_wallet(self.student)
        self.assertFalse(Wallet.objects.exists())

    def test_debit_and_retry_are_atomic(self):
        self.award()
        values = dict(student=self.student, amount=70, reason="Pack", reference="pack:test", request_id=uuid.uuid4())
        first = spend_coins(**values)
        self.assertEqual(spend_coins(**values), {**first, "replayed": True})
        self.assertEqual(Wallet.objects.get().niscoin, 30)
        self.assertEqual(WalletEntry.objects.count(), 2)

    def test_insufficient_funds_rolls_back_everything(self):
        models = (TowerPlayer, Wallet, GameOperation, WalletEntry, TowerAuditEvent)
        before = {model: model.objects.count() for model in models}
        self.assert_wallet_error("insufficient_funds", spend_coins, student=self.student, amount=1,
                                 reason="Pack", reference="pack:test", request_id=uuid.uuid4())
        for model in models:
            self.assertEqual(model.objects.count(), before[model])

    def test_audit_failure_rolls_back_balance_and_ledger(self):
        self.award()
        with patch("tower.services.wallet.TowerAuditEvent.objects.create", side_effect=RuntimeError("audit failure")):
            with self.assertRaises(RuntimeError):
                self.award(amount=25)
        self.assertEqual(Wallet.objects.get().niscoin, 100)
        self.assertEqual((WalletEntry.objects.count(), GameOperation.objects.count()), (1, 1))

    def test_balance_limit_and_database_constraint(self):
        wallet = student_wallet(self.student)
        Wallet.objects.filter(pk=wallet.pk).update(niscoin=MAX_BALANCE)
        self.assert_wallet_error("balance_limit", self.award, amount=1)
        for value in [-1, MAX_BALANCE + 1]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                Wallet.objects.filter(pk=wallet.pk).update(niscoin=value)
        self.assertFalse(WalletEntry.objects.exists())

    def test_ledger_and_audit_are_append_only_and_protect_accounts(self):
        self.award()
        for model in (WalletEntry, TowerAuditEvent):
            obj = model.objects.get(student=self.student) if model is TowerAuditEvent else model.objects.get()
            for action in [obj.save, obj.delete, lambda: model.objects.all().update(id=999), lambda: model.objects.all().delete()]:
                with self.assertRaises(ValidationError):
                    action()
        with self.assertRaises(ProtectedError):
            self.student.delete()

    def test_admin_corrections_use_separate_entries_for_both_currencies(self):
        self.award()
        values = dict(admin=self.admin, student_id=self.student.pk, currency="niscoin", delta=-25,
                      reason="Fix wrong award", request_id=uuid.uuid4())
        adjust_wallet(**values)
        self.assertTrue(adjust_wallet(**values)["replayed"])
        adjust_wallet(**{**values, "currency": "universal_shards", "delta": 4, "request_id": uuid.uuid4()})
        wallet = Wallet.objects.get()
        self.assertEqual((wallet.niscoin, wallet.universal_shards), (75, 4))
        self.assertEqual(WalletEntry.objects.count(), 3)
        self.assertEqual(WalletEntry.objects.filter(source="correction").count(), 2)

    def test_staff_cannot_correct_and_admin_cannot_overdraw(self):
        values = dict(student_id=self.student.pk, currency="niscoin", delta=-1, reason="Fix", request_id=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            adjust_wallet(admin=self.staff, **values)
        self.assert_wallet_error("insufficient_funds", adjust_wallet, admin=self.admin, **values)
        self.assertFalse(WalletEntry.objects.exists())

    def test_reconciliation_detects_cached_balance_drift(self):
        self.award()
        out = io.StringIO()
        call_command("reconcile_tower_wallets", stdout=out)
        self.assertIn("Checked 1 wallets", out.getvalue())
        Wallet.objects.update(niscoin=999)
        with self.assertRaises(CommandError):
            call_command("reconcile_tower_wallets", stdout=out)
        self.assertEqual(Wallet.objects.get().niscoin, 999)  # Reconciliation never repairs silently.

    def test_student_api_only_returns_own_wallet_and_escapes_reason(self):
        self.award(reason="<script>bad</script>")
        self.award(teacher=self.other_teacher, student_id=self.other_student.pk, class_id=self.other_group.pk, amount=900)
        client = self.account_client()
        response = client.get(reverse("tower:wallet"), {"student_id": self.other_student.pk})
        self.assertEqual(response.json()["wallet"]["niscoin"], 100)
        self.assertEqual(len(response.json()["entries"]), 1)
        self.assertIn("no-store", response["Cache-Control"])
        page = client.get(reverse("tower:home"), {"tab": "wallet"})
        self.assertContains(page, "&lt;script&gt;bad&lt;/script&gt;")
        self.assertNotContains(page, "<script>bad</script>")

    def test_cursor_pagination_has_no_duplicates(self):
        for _ in range(28):
            self.award(amount=1)
        client = self.account_client()
        first = client.get(reverse("tower:wallet")).json()
        second = client.get(reverse("tower:wallet"), {"before": first["next_before"]}).json()
        self.assertEqual((len(first["entries"]), len(second["entries"])), (25, 3))
        ids = [row["id"] for row in first["entries"] + second["entries"]]
        self.assertEqual(len(set(ids)), 28)
        self.assertIsNone(second["next_before"])
        self.assertEqual(client.get(reverse("tower:wallet"), {"before": "bad"}).status_code, 400)

    def test_award_api_and_html_form_share_idempotency(self):
        key = str(uuid.uuid4())
        response = self.api_award(request_id=key)
        self.assertEqual(response.status_code, 200)
        client = self.account_client(self.teacher)
        response = client.post(reverse("tower:teacher_wallets"), {
            "student_id": self.student.pk, "class_id": self.group.pk, "amount": 50,
            "reason": "Coding achievement", "request_id": key,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(WalletEntry.objects.count(), 1)
        self.assertEqual(client.get(reverse("tower:teacher_wallets")).status_code, 200)

    def test_api_rejects_malformed_json_extra_fields_and_numeric_coercion(self):
        client = self.account_client(self.teacher)
        self.assertEqual(client.post(reverse("tower:award"), "[]", content_type="application/json").status_code, 400)
        self.assertEqual(client.post(reverse("tower:award"), "{", content_type="application/json").status_code, 400)
        self.assertEqual(self.api_award(client, unexpected=1).status_code, 400)
        for amount in [True, 1.5, "5", 0, -10]:
            self.assertEqual(self.api_award(client, amount=amount).status_code, 400)
        self.assertFalse(Wallet.objects.exists())

    def test_api_blocks_anonymous_student_and_unassigned_teacher(self):
        self.assertEqual(self.api_award(self.client).status_code, 401)
        self.assertEqual(self.api_award(self.account_client()).status_code, 403)
        Teacher.objects.filter(pk=self.teacher.pk).update(can_manage_tower=False)
        self.assertEqual(self.api_award(self.account_client(self.teacher)).status_code, 403)

    def test_csrf_is_required_and_valid_token_is_accepted(self):
        client = self.account_client(self.teacher, csrf=True)
        self.assertEqual(self.api_award(client).status_code, 403)
        self.assertEqual(client.get(reverse("tower:teacher_wallets")).status_code, 200)
        response = client.post(reverse("tower:teacher_wallets"), {
            "class_id": self.group.pk, "student_id": self.student.pk, "amount": 10,
            "reason": "Quiz", "request_id": uuid.uuid4(), "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        })
        self.assertEqual(response.status_code, 302)

    def test_teacher_history_hides_other_teacher_awards(self):
        StudentClassMembership.objects.create(student=self.student, class_group=self.other_group)
        self.award()
        self.award(teacher=self.other_teacher, class_id=self.other_group.pk, amount=20, reason="Other private reason")
        client = self.account_client(self.teacher)
        response = client.get(reverse("tower:teacher_history", args=[self.student.pk]), {"class_id": self.group.pk})
        self.assertEqual(response.json()["niscoin"], 120)
        self.assertEqual(len(response.json()["entries"]), 1)
        self.assertNotIn("Other private reason", response.content.decode())
        self.assertEqual(client.get(reverse("tower:teacher_history", args=[self.other_student.pk]), {"class_id": self.group.pk}).status_code, 404)
        self.assertEqual(client.get(reverse("tower:teacher_wallets"), {"class_id": self.other_group.pk}).status_code, 404)

    def test_teacher_page_empty_and_validation_errors(self):
        client = self.account_client(self.teacher)
        response = client.post(reverse("tower:teacher_wallets"), {
            "class_id": self.group.pk, "student_id": self.student.pk, "amount": 10001,
            "reason": "", "request_id": uuid.uuid4(),
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Wallet.objects.exists())
        ClassGroup.objects.filter(pk=self.group.pk).update(tower_enabled=False)
        self.assertEqual(client.get(reverse("tower:teacher_wallets")).status_code, 200)

    def test_wallet_admin_is_readonly_and_hidden_from_staff(self):
        self.award()
        self.staff.user_permissions.set(Permission.objects.filter(content_type__app_label="tower"))
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("admin:tower_wallet_changelist")).status_code, 403)
        url = reverse("admin:tower_wallet_adjust", args=[Wallet.objects.get().pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin:tower_wallet_change", args=[Wallet.objects.get().pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("admin:tower_wallet_change", args=[Wallet.objects.get().pk]), {"niscoin": 999}).status_code, 403)
        response = self.client.post(url, {"currency": "niscoin", "delta": -20, "reason": "Correction", "request_id": uuid.uuid4()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Wallet.objects.get().niscoin, 80)
        response = self.client.post(url, {"currency": "niscoin", "delta": -100, "reason": "Correction", "request_id": uuid.uuid4()})
        self.assertContains(response, "Недостаточно средств")

    def test_access_changes_and_admin_grants_have_audit(self):
        client = self.account_client(self.teacher)
        response = client.patch(reverse("tower:class_access", args=[self.group.pk]), json.dumps({"tower_enabled": False}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        event = TowerAuditEvent.objects.get(event_type="class_access")
        self.assertEqual(event.metadata, {"before": True, "after": False})
        self.assertEqual(event.actor_teacher_id, self.teacher.pk)
        request = RequestFactory().post("/admin/")
        request.user = self.admin
        self.teacher.can_manage_tower = False
        admin_site.site._registry[Teacher].save_model(request, self.teacher, None, True)
        self.assertEqual(TowerAuditEvent.objects.filter(event_type="teacher_access").count(), 1)

    def test_student_delete_returns_conflict_when_wallet_has_history(self):
        self.award()
        response = self.account_client(self.teacher).delete(f"/api/teacher/students/{self.student.pk}/")
        self.assertEqual(response.status_code, 409)

    def test_access_audit_failure_rolls_back_class_flag(self):
        with patch("tower.services.audit.TowerAuditEvent.objects.create", side_effect=RuntimeError("audit failure")):
            with self.assertLogs("core.middleware", level="ERROR"):
                response = self.account_client(self.teacher).patch(reverse("tower:class_access", args=[self.group.pk]),
                    json.dumps({"tower_enabled": False}), content_type="application/json")
            self.assertEqual(response.status_code, 500)
        self.group.refresh_from_db()
        self.assertTrue(self.group.tower_enabled)

    def test_revocation_prevents_replaying_existing_award(self):
        key = uuid.uuid4()
        self.award(request_id=key)
        Teacher.objects.filter(pk=self.teacher.pk).update(can_manage_tower=False)
        with self.assertRaises(PermissionDenied):
            self.award(request_id=key)

    def test_admin_correction_requires_reason_and_valid_currency(self):
        values = dict(admin=self.admin, student_id=self.student.pk, currency="niscoin", delta=1, reason="Fix", request_id=uuid.uuid4())
        self.assert_wallet_error("reason_required", adjust_wallet, **{**values, "reason": " "})
        self.assert_wallet_error("invalid_currency", adjust_wallet, **{**values, "currency": "money"})
        for delta in [0, True, 1.5, 1000001]:
            self.assert_wallet_error("invalid_amount", adjust_wallet, **{**values, "delta": delta})
        self.assertFalse(Wallet.objects.exists())
