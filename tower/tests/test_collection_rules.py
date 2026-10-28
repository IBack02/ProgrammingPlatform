import importlib
import io
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, override_settings

from tower.models import (CharacterSetEntitlement, HeroDefinition, PackOffer, PackOpening, PackVersion,
                          PlayerCharacterSet, PlayerCosmetic, PlayerHero, TowerPlayer, Wallet)
from tower.services.collection import _legendary_slot, exchange_fragments, legendary_chance, open_pack
from .wallet_fixtures import WalletFixtures


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CollectionRulesTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()

    def pack(self, key=None, version="school-v2"):
        return open_pack(student=self.student, offer="school", version=version, request_id=key or uuid.uuid4())

    def test_thresholds_and_seed_match_frozen_migration(self):
        self.assertEqual(set(HeroDefinition.objects.filter(rarity="epic").values_list("required_fragments", flat=True)), {6})
        self.assertEqual(set(HeroDefinition.objects.filter(rarity="legendary").values_list("required_fragments", flat=True)), {1})
        call_command("seed_tower_catalog", stdout=io.StringIO())
        self.assertEqual(PackVersion.objects.count(), 2)
        self.assertEqual(PackOffer.objects.get(pk="school").version.slug, "school-v2")

    def test_exact_legendary_roll_boundaries_and_cap(self):
        config = PackVersion.objects.get(slug="school-v2").config
        for position, ticket, hit in [(0, 9939, False), (0, 9940, True), (0, 9999, True),
                                      (9, 9499, False), (9, 9500, True), (9, 9999, True)]:
            chance = legendary_chance(config, position)
            with patch("tower.services.collection.secrets.randbelow", side_effect=[ticket, 2]):
                self.assertEqual(_legendary_slot(config, chance), 2 if hit else None)
        self.assertEqual(legendary_chance(config, 10), 60)
        self.assertTrue(all(60 <= value <= 500 for value in config["legendary_cycle_bps"]))
        self.assertEqual(sorted(config["legendary_cycle_bps"]), config["legendary_cycle_bps"])

    @patch("tower.services.collection._draw", return_value=("nis-coder", "common"))
    @patch("tower.services.collection._legendary_slot", return_value=None)
    def test_ten_pack_cycle_replay_and_reset(self, gate, draw):
        self.award(amount=600)
        chances = []
        for index in range(11):
            key = uuid.uuid4()
            result = self.pack(key)
            chances.append(result["legendary_chance_bps"])
            self.assertEqual(result["cycle_before"], index % 10)
            self.assertEqual(result["cycle_after"], (index + 1) % 10)
            self.assertTrue(self.pack(key)["replayed"])
        self.assertEqual(chances, [60, 109, 158, 207, 256, 304, 353, 402, 451, 500, 60])
        self.assertEqual((PackOpening.objects.count(), TowerPlayer.objects.get().pack_cycle_position), (11, 1))
        self.assertEqual(Wallet.objects.get().niscoin, 50)

    @patch("tower.services.collection._draw", return_value=("nis-coder", "common"))
    @patch("tower.services.collection._legendary_slot", return_value=1)
    @patch("tower.services.collection.secrets.choice", side_effect=lambda values: values[0])
    def test_legendary_one_fragment_unlocks_set_without_resetting_cycle(self, choice, gate, draw):
        self.award(amount=50)
        TowerPlayer.objects.update(pack_cycle_position=8, pack_misses=4)
        result = self.pack()
        drop = result["drops"][1]
        self.assertEqual((drop["rarity"], drop["quantity"], drop["required"], drop["added"]), ("legendary", 1, 1, 1))
        self.assertTrue(drop["completed"])
        self.assertEqual(PlayerCosmetic.objects.count(), 6)
        self.assertEqual((result["cycle_before"], result["cycle_after"], result["legendary_chance_bps"]), (8, 9, 451))
        self.assertEqual(TowerPlayer.objects.get().pack_cycle_position, 9)
        self.assertEqual(legendary_chance(PackOffer.objects.get().version.config, 9), 500)

    def test_six_epic_fragments_unlock_once_and_reconcile_wallet(self):
        self.shards_for_test()
        result = exchange_fragments(student=self.student, hero_id="nis-physicist", quantity=6, request_id=uuid.uuid4())
        self.assertTrue(result["drop"]["completed"])
        self.assertEqual(PlayerCosmetic.objects.count(), 6)
        self.assertEqual(Wallet.objects.get().universal_shards, 52)
        call_command("reconcile_tower_wallets", stdout=io.StringIO())

    def shards_for_test(self):
        from tower.services.wallet import adjust_wallet
        adjust_wallet(admin=self.admin, student_id=self.student.pk, currency="universal_shards", delta=100,
                      reason="Test fragments", request_id=uuid.uuid4())

    @patch("tower.services.collection._legendary_slot", return_value=None)
    @patch("tower.services.collection._draw", return_value=("nis-coder", "common"))
    def test_failure_rolls_back_cycle_and_money(self, draw, gate):
        self.award(amount=50)
        TowerPlayer.objects.update(pack_cycle_position=9)
        with patch("tower.services.collection.PackOpening.objects.create", side_effect=RuntimeError("storage failed")):
            with self.assertRaises(RuntimeError):
                self.pack()
        self.assertEqual(TowerPlayer.objects.get().pack_cycle_position, 9)
        self.assertEqual(Wallet.objects.get().niscoin, 50)
        self.assertFalse(PackOpening.objects.exists())

    @patch("tower.services.collection._draw", return_value=("nis-coder", "common"))
    def test_old_receipt_replays_after_upgrade_without_advancing_new_cycle(self, draw):
        self.award(amount=100)
        offer = PackOffer.objects.get(pk="school")
        old = PackVersion.objects.get(slug="school-v1")
        PackOffer.objects.filter(pk=offer.pk).update(version=old)
        key = uuid.uuid4()
        first = self.pack(key, version="school-v1")
        PackOffer.objects.filter(pk=offer.pk).update(version=offer.version)
        replay = self.pack(key, version="school-v1")
        self.assertEqual(replay, {**first, "replayed": True})
        self.assertEqual(TowerPlayer.objects.get().pack_cycle_position, 0)
        self.assertEqual((PackOpening.objects.count(), Wallet.objects.get().niscoin), (1, 50))

    def test_migration_unlocks_existing_progress_and_keeps_grants_idempotent(self):
        self.award(amount=50)
        player = TowerPlayer.objects.get()
        HeroDefinition.objects.filter(rarity__in=["epic", "legendary"]).update(required_fragments=10)
        PlayerHero.objects.create(player=player, hero_id="nis-physicist", fragments=6)
        PlayerHero.objects.create(player=player, hero_id="nis-musician", fragments=2)
        PlayerHero.objects.create(player=player, hero_id="nis-debater", fragments=4)
        frozen_v1 = PackVersion.objects.get(slug="school-v1").config
        PackOffer.objects.update(version=PackVersion.objects.get(slug="school-v1"), is_active=False)
        apps = MigrationExecutor(connection).loader.project_state().apps
        migration = importlib.import_module("tower.migrations.0012_collection_rules_v2")
        for _ in range(2):
            migration.publish_rules(apps, SimpleNamespace(connection=connection))
        self.assertEqual(PlayerHero.objects.filter(assembled_at__isnull=False).count(), 2)
        self.assertEqual(PlayerHero.objects.get(hero_id="nis-musician").fragments, 2)
        self.assertEqual((CharacterSetEntitlement.objects.count(), PlayerCharacterSet.objects.count(), PlayerCosmetic.objects.count()), (2, 2, 12))
        self.assertEqual(PackVersion.objects.get(slug="school-v1").config, frozen_v1)
        self.assertFalse(PackOffer.objects.get().is_active)
        self.assertEqual(Wallet.objects.get().niscoin, 50)
