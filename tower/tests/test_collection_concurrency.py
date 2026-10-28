import io
import uuid
from unittest import skipUnless

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase, override_settings

from tower.models import PackOpening, PlayerCosmetic, PlayerHero, TowerPlayer, Wallet
from tower.services.collection import buy_cosmetic, exchange_fragments, open_pack
from tower.services.wallet import adjust_wallet
from . import test_wallet_concurrency as wallet_races
from .wallet_fixtures import WalletFixtures


@skipUnless(connection.vendor == "postgresql", "Collection races require PostgreSQL row locks")
@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CollectionConcurrencyTests(WalletFixtures, TransactionTestCase):
    parallel = wallet_races.WalletConcurrencyTests.parallel

    def setUp(self):
        self.create_accounts()
        call_command("seed_tower_catalog", stdout=io.StringIO())

    def pack(self, key):
        return open_pack(student=self.student, offer="school", version="school-v2", request_id=key)

    def test_identical_request_keeps_one_charge_and_one_random_result(self):
        self.award(amount=100)
        key = uuid.uuid4()
        results = self.parallel(lambda: self.pack(key), lambda: self.pack(key))
        self.assertTrue(all(kind == "ok" for kind, result in results))
        self.assertEqual(sorted(result["replayed"] for kind, result in results), [False, True])
        self.assertEqual(results[0][1]["drops"], results[1][1]["drops"])
        self.assertEqual((Wallet.objects.get().niscoin, PackOpening.objects.count()), (50, 1))
        self.assertEqual(TowerPlayer.objects.get().pack_cycle_position, 1)

    def test_two_different_packs_advance_cycle_without_losing_a_step(self):
        self.award(amount=100)
        TowerPlayer.objects.update(pack_cycle_position=9)
        results = self.parallel(lambda: self.pack(uuid.uuid4()), lambda: self.pack(uuid.uuid4()))
        self.assertTrue(all(kind == "ok" for kind, result in results))
        self.assertEqual(sorted(result["legendary_chance_bps"] for kind, result in results), [60, 500])
        self.assertEqual(TowerPlayer.objects.get().pack_cycle_position, 1)
        self.assertEqual((Wallet.objects.get().niscoin, PackOpening.objects.count()), (0, 2))

    def test_two_packs_cannot_spend_the_same_coins(self):
        self.award(amount=50)
        results = self.parallel(lambda: self.pack(uuid.uuid4()), lambda: self.pack(uuid.uuid4()))
        self.assertEqual(sorted(kind for kind, result in results), ["error", "ok"])
        self.assertIn(("error", "insufficient_funds"), results)
        self.assertEqual((Wallet.objects.get().niscoin, PackOpening.objects.count()), (0, 1))

    def test_same_cosmetic_cannot_be_bought_twice(self):
        self.award(amount=100)
        def buy():
            return buy_cosmetic(student=self.student, cosmetic_id="cap", request_id=uuid.uuid4())
        results = self.parallel(buy, buy)
        self.assertEqual(sorted(kind for kind, result in results), ["error", "ok"])
        self.assertIn(("error", "already_owned"), results)
        self.assertEqual((Wallet.objects.get().niscoin, PlayerCosmetic.objects.count()), (70, 1))

    def test_concurrent_exchange_cannot_overfill_or_charge_twice(self):
        adjust_wallet(admin=self.admin, student_id=self.student.pk, currency="universal_shards", delta=100, reason="Test", request_id=uuid.uuid4())
        def exchange():
            return exchange_fragments(student=self.student, hero_id="nis-coder", quantity=10, request_id=uuid.uuid4())
        results = self.parallel(exchange, exchange)
        self.assertEqual(sorted(kind for kind, result in results), ["error", "ok"])
        self.assertIn(("error", "already_collected"), results)
        self.assertEqual((Wallet.objects.get().universal_shards, PlayerHero.objects.get().fragments), (80, 10))
