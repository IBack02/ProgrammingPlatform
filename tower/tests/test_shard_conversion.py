import io
import importlib
import uuid
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tower.models import MAX_BALANCE, GameOperation, HeroDefinition, PackOffer, PackVersion, PlayerHero, TowerPlayer, Wallet, WalletEntry
from tower.services.collection import convert_shards, open_pack
from tower.services.wallet import WalletError, adjust_wallet
from .wallet_fixtures import WalletFixtures
from . import test_wallet_concurrency as wallet_races


class ConversionFixtures(WalletFixtures):
    def shards(self, amount=20):
        adjust_wallet(admin=self.admin, student_id=self.student.pk, currency="universal_shards", delta=amount,
                      reason="Conversion test", request_id=uuid.uuid4())

    def convert(self, quantity=3, key=None):
        return convert_shards(student=self.student, quantity=quantity, request_id=key or uuid.uuid4())


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ShardConversionTests(ConversionFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()

    def test_conversion_credits_five_coins_per_shard_once_with_two_journal_entries(self):
        self.shards()
        key = uuid.uuid4()
        result = self.convert(key=key)
        wallet = Wallet.objects.get()
        self.assertEqual((wallet.universal_shards, wallet.niscoin), (17, 15))
        entries = WalletEntry.objects.filter(operation__action="shards_to_coins")
        self.assertEqual(set(entries.values_list("currency", "delta")), {("universal_shards", -3), ("niscoin", 15)})
        self.assertEqual(set(entries.values_list("source", flat=True)), {"shard_conversion"})
        self.assertTrue(self.convert(key=key)["replayed"])
        self.assertEqual(GameOperation.objects.filter(action="shards_to_coins").count(), 1)
        self.assertEqual(entries.count(), 2)
        self.assertEqual(result["coins_received"], 15)
        with self.assertRaises(WalletError):
            self.convert(quantity=4, key=key)
        call_command("reconcile_tower_wallets", stdout=io.StringIO())

    def test_invalid_quantities_and_insufficient_shards_do_not_credit(self):
        self.shards(2)
        for quantity in (True, 0, -1, 1.5, "1", MAX_BALANCE):
            with self.subTest(quantity=quantity), self.assertRaises(WalletError):
                self.convert(quantity)
        with self.assertRaises(WalletError):
            self.convert(3)
        self.assertEqual((Wallet.objects.get().universal_shards, Wallet.objects.get().niscoin), (2, 0))

    def test_balance_cap_and_failed_credit_leave_both_balances_unchanged(self):
        self.shards()
        Wallet.objects.update(niscoin=MAX_BALANCE)
        with self.assertRaises(WalletError):
            self.convert(1)
        self.assertEqual(Wallet.objects.get().universal_shards, 20)
        Wallet.objects.update(niscoin=0)
        create = WalletEntry.objects.create
        def fail_credit(**values):
            if values["currency"] == "niscoin":
                raise RuntimeError("credit storage failed")
            return create(**values)
        with patch("tower.services.collection.WalletEntry.objects.create", side_effect=fail_credit):
            with self.assertRaises(RuntimeError):
                self.convert()
        self.assertEqual((Wallet.objects.get().universal_shards, Wallet.objects.get().niscoin), (20, 0))
        self.assertFalse(GameOperation.objects.filter(action="shards_to_coins").exists())

    def test_http_schema_csrf_permissions_and_html_exchange(self):
        self.shards()
        url = reverse("tower:student_action_api", args=["convert"])
        values = {"quantity": 3, "request_id": str(uuid.uuid4())}
        self.assertEqual(self.account_client(csrf=True).post(url, values, content_type="application/json").status_code, 403)
        self.assertEqual(self.account_client(self.teacher).post(url, values, content_type="application/json").status_code, 403)
        self.assertEqual(self.account_client(collection_access=False).post(url, values, content_type="application/json").status_code, 403)
        client = self.account_client()
        self.assertEqual(client.post(url, {**values, "rate": 1000}, content_type="application/json").status_code, 400)
        result = client.post(reverse("tower:student_action", args=["convert"]), values)
        self.assertRedirects(result, reverse("tower:home") + "?tab=wallet")
        self.assertEqual(Wallet.objects.get().niscoin, 15)
        self.group.tower_enabled = False
        self.group.save(update_fields=["tower_enabled"])
        self.assertEqual(client.post(url, values, content_type="application/json").status_code, 403)

    def test_new_pack_overflow_rewards_each_rarity_and_preserves_legendary_cycle(self):
        self.award(amount=250)
        player = TowerPlayer.objects.get()
        for hero in HeroDefinition.objects.all():
            PlayerHero.objects.create(player=player, hero=hero, fragments=hero.required_fragments, assembled_at=timezone.now())
        for hero, rarity, per_slot in [("nis-coder", "common", 2), ("nis-artist", "rare", 4), ("nis-physicist", "epic", 8)]:
            with patch("tower.services.collection._legendary_slot", return_value=None), patch("tower.services.collection._draw", return_value=(hero, rarity)):
                result = open_pack(student=self.student, offer="school", version="school-v3", request_id=uuid.uuid4())
            self.assertEqual([drop["shards"] for drop in result["drops"]], [per_slot] * 3)
        with patch("tower.services.collection._legendary_slot", return_value=0), patch("tower.services.collection.secrets.choice", return_value="nis-musician"), patch("tower.services.collection._draw", return_value=("nis-coder", "common")):
            result = open_pack(student=self.student, offer="school", version="school-v3", request_id=uuid.uuid4())
        self.assertEqual(result["drops"][0]["shards"], 16)
        self.assertEqual(result["cycle_after"], 4)
        call_command("reconcile_tower_wallets", stdout=io.StringIO())

    def test_fully_duplicate_pack_long_run_return_below_price_even_at_max_legendary_chance(self):
        config = PackOffer.objects.get(pk="school").version.config
        p = max(config["legendary_cycle_bps"]) / 10000
        weights, rates = config["weights"], config["overflow_rates"]
        total = sum(weights.values())
        ordinary = sum(weights[r] * rates[r] * config["fragments_per_slot"] for r in ("common", "rare", "epic")) / total
        rare = (weights["rare"] * rates["rare"] + weights["epic"] * rates["epic"]) * config["fragments_per_slot"] / (weights["rare"] + weights["epic"])
        q = (1 - p) * (weights["common"] / total) ** config["slots"]
        protected = q ** config["pity_after"] / sum(q ** i for i in range(config["pity_after"] + 1))
        expected_shards = config["slots"] * ordinary + p * (rates["legendary"] - ordinary) + protected * (rare - ordinary) * (1 - p / config["slots"])
        self.assertLess(expected_shards * 5, config["price"])

    def test_reward_migration_is_frozen_and_preserves_old_versions_and_paused_newer_offers(self):
        old = PackVersion.objects.get(slug="school-v2")
        old_config = old.config
        PackOffer.objects.filter(pk="school").update(version=old, is_active=False)
        apps = MigrationExecutor(connection).loader.project_state().apps
        migrate = importlib.import_module("tower.migrations.0014_pack_rewards_and_conversion").publish_pack
        with patch("pathlib.Path.read_text", side_effect=AssertionError("Migration must use frozen data")):
            migrate(apps, SimpleNamespace(connection=connection))
            migrate(apps, SimpleNamespace(connection=connection))
        self.assertEqual(PackOffer.objects.get(pk="school").version.slug, "school-v3")
        self.assertFalse(PackOffer.objects.get(pk="school").is_active)
        self.assertEqual(PackVersion.objects.get(pk=old.pk).config, old_config)
        newer = PackVersion.objects.create(slug="school-v4", config={"future": True})
        PackOffer.objects.filter(pk="school").update(version=newer)
        migrate(apps, SimpleNamespace(connection=connection))
        self.assertEqual(PackOffer.objects.get(pk="school").version_id, newer.pk)
        self.assertFalse(Wallet.objects.exists())


@skipUnless(connection.vendor == "postgresql", "Shard conversion races require PostgreSQL row locks")
@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ShardConversionConcurrencyTests(ConversionFixtures, TransactionTestCase):
    parallel = wallet_races.WalletConcurrencyTests.parallel

    def setUp(self):
        self.create_accounts()
        self.shards(10)

    def test_identical_conversion_request_is_applied_once(self):
        key = uuid.uuid4()
        results = self.parallel(lambda: self.convert(key=key), lambda: self.convert(key=key))
        self.assertTrue(all(kind == "ok" for kind, _ in results))
        self.assertEqual((Wallet.objects.get().niscoin, Wallet.objects.get().universal_shards), (15, 7))
        self.assertEqual(WalletEntry.objects.filter(operation__action="shards_to_coins").count(), 2)

    def test_competing_conversions_cannot_spend_the_same_shards(self):
        results = self.parallel(lambda: self.convert(6), lambda: self.convert(6))
        self.assertEqual(sorted(kind for kind, _ in results), ["error", "ok"])
        self.assertEqual((Wallet.objects.get().niscoin, Wallet.objects.get().universal_shards), (30, 4))
