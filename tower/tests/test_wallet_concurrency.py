import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings

from core.models import StudentClassMembership
from tower.models import GameOperation, TowerAuditEvent, TowerPlayer, Wallet, WalletEntry
from tower.services.wallet import WalletError, spend_coins, student_wallet
from .wallet_fixtures import WalletFixtures


@skipUnless(connection.vendor == "postgresql", "Row-lock races must run against PostgreSQL")
@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WalletConcurrencyTests(WalletFixtures, TransactionTestCase):
    def setUp(self):
        self.create_accounts()

    def parallel(self, *jobs):
        barrier = Barrier(len(jobs))

        def execute(job):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return ("ok", job())
                except WalletError as exc:
                    return ("error", exc.code)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = [pool.submit(execute, job) for job in jobs]
            return [future.result(timeout=30) for future in futures]

    def test_simultaneous_first_visits_create_one_wallet(self):
        results = self.parallel(lambda: student_wallet(self.student).pk, lambda: student_wallet(self.student).pk)
        self.assertEqual(results[0], results[1])
        self.assertEqual((TowerPlayer.objects.count(), Wallet.objects.count()), (1, 1))

    def test_same_request_awards_once(self):
        key = uuid.uuid4()
        results = self.parallel(lambda: self.award(request_id=key), lambda: self.award(request_id=key))
        self.assertTrue(all(kind == "ok" for kind, _ in results))
        self.assertEqual(sorted(row["replayed"] for _, row in results), [False, True])
        self.assertEqual(Wallet.objects.get().niscoin, 100)
        self.assertEqual((GameOperation.objects.count(), WalletEntry.objects.count(), TowerAuditEvent.objects.count()), (1, 1, 1))

    def test_independent_awards_do_not_lose_updates(self):
        results = self.parallel(lambda: self.award(amount=30), lambda: self.award(amount=70), lambda: self.award(amount=20))
        self.assertTrue(all(kind == "ok" for kind, _ in results))
        self.assertEqual(Wallet.objects.get().niscoin, 120)
        self.assertEqual(list(WalletEntry.objects.order_by("id").values_list("balance_after", flat=True))[-1], 120)

    def test_two_debits_cannot_spend_the_same_balance(self):
        self.award(amount=100)

        def buy():
            return spend_coins(student=self.student, amount=80, reason="Pack", reference="test:pack", request_id=uuid.uuid4())

        results = self.parallel(buy, buy)
        self.assertEqual(sorted(kind for kind, _ in results), ["error", "ok"])
        self.assertIn(("error", "insufficient_funds"), results)
        self.assertEqual(Wallet.objects.get().niscoin, 20)
        self.assertEqual((GameOperation.objects.count(), WalletEntry.objects.count()), (2, 2))

    def test_same_request_for_different_students_cannot_award_both(self):
        StudentClassMembership.objects.create(student=self.other_student, class_group=self.group)
        key = uuid.uuid4()
        results = self.parallel(lambda: self.award(request_id=key),
                                lambda: self.award(request_id=key, student_id=self.other_student.pk))
        self.assertEqual(sorted(kind for kind, _ in results), ["error", "ok"])
        self.assertIn(("error", "request_conflict"), results)
        self.assertEqual(WalletEntry.objects.count(), 1)
        self.assertEqual(sum(Wallet.objects.values_list("niscoin", flat=True)), 100)
