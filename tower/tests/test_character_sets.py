import copy
import importlib
import io
import uuid
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tower.character_contracts import load_behavior_profiles, load_character_sets
from tower.models import (
    CharacterSetEntitlement, CharacterSetPublication, CharacterSetVersion, CosmeticDefinition,
    GameOperation, PackOpening, PlayerCharacterSet, PlayerCosmetic, PlayerHero, TowerAuditEvent,
    TowerPlayer, Wallet, WalletEntry,
)
from tower.services.character_sets import grant_character_set, restore_character_sets
from tower.services.collection import buy_cosmetic, exchange_fragments, open_pack, save_avatar
from tower.services.set_publication import publish_character_set
from tower.services.wallet import WalletError, adjust_wallet
from .wallet_fixtures import WalletFixtures
from . import test_wallet_concurrency as wallet_races


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CharacterSetGrantTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()
        call_command("seed_tower_catalog", stdout=io.StringIO())

    def pack(self, key=None):
        return open_pack(student=self.student, offer="school", version="school-v1", request_id=key or uuid.uuid4())

    def card(self, hero="nis-coder", fragments=10, assembled=True):
        player = TowerPlayer.objects.get(student=self.student)
        return PlayerHero.objects.create(player=player, hero_id=hero, fragments=fragments,
                                         assembled_at=timezone.now() if assembled else None)

    @patch("tower.services.collection._draw", return_value=("nis-coder", "common"))
    def test_pack_first_assembly_grants_six_items_once_and_persists_origin(self, draw):
        self.award(amount=100)
        self.card(fragments=8, assembled=False)
        key = uuid.uuid4()
        first = self.pack(key)
        self.assertTrue(first["drops"][0]["character_set"]["newly_granted"])
        self.assertFalse(first["drops"][1]["character_set"]["newly_granted"])
        self.assertEqual((CharacterSetEntitlement.objects.count(), PlayerCharacterSet.objects.count(), PlayerCosmetic.objects.count()), (1, 1, 6))
        entitlement = CharacterSetEntitlement.objects.get()
        self.assertEqual(entitlement.operation_id, PackOpening.objects.get().operation_id)
        self.assertTrue(all(row.source == "character_set" and row.operation_id is None for row in PlayerCosmetic.objects.all()))
        self.assertEqual(self.pack(key), {**first, "replayed": True})
        self.assertEqual(PlayerCosmetic.objects.count(), 6)
        self.assertEqual(Wallet.objects.get().niscoin, 50)

    def test_exchange_assembly_grants_set_without_extra_currency_operations(self):
        adjust_wallet(admin=self.admin, student_id=self.student.pk, currency="universal_shards", delta=100,
                      reason="Test", request_id=uuid.uuid4())
        before = WalletEntry.objects.count()
        result = exchange_fragments(student=self.student, hero_id="nis-artist", quantity=10, request_id=uuid.uuid4())
        self.assertTrue(result["drop"]["character_set"]["granted"])
        self.assertEqual(PlayerCosmetic.objects.count(), 6)
        self.assertEqual(WalletEntry.objects.count(), before + 1)
        self.assertEqual(Wallet.objects.get().universal_shards, 60)

    @patch("tower.services.collection._draw", return_value=("nis-coder", "common"))
    def test_failing_set_grant_rolls_back_pack_money_fragments_receipt_and_right(self, draw):
        self.award(amount=50)
        card = self.card(fragments=8, assembled=False)
        with patch("tower.services.character_sets.PlayerCosmetic.objects.create", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                self.pack()
        card.refresh_from_db()
        self.assertEqual((card.fragments, card.assembled_at), (8, None))
        self.assertEqual(Wallet.objects.get().niscoin, 50)
        self.assertFalse(CharacterSetEntitlement.objects.exists())
        self.assertFalse(PlayerCharacterSet.objects.exists())
        self.assertFalse(PackOpening.objects.exists())
        self.assertEqual(GameOperation.objects.count(), 1)

    def test_backfill_preserves_purchases_avatar_wallet_and_is_repeatable(self):
        self.award(amount=100)
        purchase = buy_cosmetic(student=self.student, cosmetic_id="cap", request_id=uuid.uuid4())
        save_avatar(student=self.student, shape="triangle", color="#10b981",
                    accessories={"head": "cap", "glasses": "", "mustache": ""}, theme="midnight")
        self.card()
        entries = list(WalletEntry.objects.values_list("pk", "delta", "balance_after"))
        first = restore_character_sets()
        self.assertEqual(first["granted"], 1)
        self.assertEqual(restore_character_sets()["granted"], 0)
        self.assertEqual(PlayerCosmetic.objects.count(), 7)
        old = PlayerCosmetic.objects.get(cosmetic_id="cap")
        self.assertEqual(old.operation.result["entry_id"], purchase["entry_id"])
        self.assertEqual(old.source, "purchase")
        self.assertEqual(list(WalletEntry.objects.values_list("pk", "delta", "balance_after")), entries)
        player = TowerPlayer.objects.get()
        self.assertEqual((player.avatar_shape, player.avatar_accessories), ("triangle", {"head": "cap"}))

    def test_unpublished_or_paused_set_retains_permanent_entitlement(self):
        self.award(amount=50)
        CharacterSetPublication.objects.filter(hero_id="nis-biologist").update(is_active=False)
        card = self.card(hero="nis-biologist")
        result = grant_character_set(player=card.player, hero=card.hero)
        self.assertTrue(result["entitled"])
        self.assertFalse(result["granted"])
        self.assertEqual(CharacterSetEntitlement.objects.get().hero_id, "nis-biologist")
        self.assertFalse(PlayerCosmetic.objects.exists())
        self.assertEqual(Wallet.objects.get().niscoin, 50)

    def test_paused_publication_can_resume_without_duplicate_or_lost_rewards(self):
        self.award(amount=50)
        card = self.card()
        CharacterSetPublication.objects.filter(hero_id="nis-coder").update(is_active=False)
        self.assertEqual(restore_character_sets()["pending"], 1)
        self.assertFalse(PlayerCharacterSet.objects.exists())
        CharacterSetPublication.objects.filter(hero_id="nis-coder").update(is_active=True)
        self.assertEqual(restore_character_sets()["granted"], 1)
        self.assertEqual(restore_character_sets()["granted"], 0)
        self.assertEqual(CharacterSetEntitlement.objects.count(), 1)

    def test_incomplete_card_or_other_players_operation_cannot_claim_reward(self):
        self.award(amount=50)
        card = self.card(fragments=9, assembled=False)
        with self.assertRaises(ValidationError):
            grant_character_set(player=card.player, hero=card.hero)
        card.fragments, card.assembled_at = 10, timezone.now()
        card.save()
        other = TowerPlayer.objects.create(student=self.other_student)
        operation = GameOperation.objects.create(player=other, actor_scope="test", action="test", request_id=uuid.uuid4(), payload_hash="test")
        with self.assertRaises(ValidationError):
            grant_character_set(player=card.player, hero=card.hero, operation=operation)
        self.assertFalse(CharacterSetEntitlement.objects.exists())

    def test_component_cannot_be_purchased_or_used_through_legacy_avatar_endpoint(self):
        self.award(amount=100)
        self.card()
        restore_character_sets()
        with self.assertRaises(WalletError):
            buy_cosmetic(student=self.student, cosmetic_id="nis-coder-head", request_id=uuid.uuid4())
        with self.assertRaises(WalletError):
            save_avatar(student=self.student, shape="square", color="#3b82f6",
                        accessories={"head": "nis-coder-head", "glasses": "", "mustache": ""}, theme="midnight")
        self.assertEqual(Wallet.objects.get().niscoin, 100)

    def test_student_sees_received_or_pending_set_and_shop_keeps_purchase_items_only(self):
        self.award(amount=50)
        CharacterSetPublication.objects.filter(hero_id="nis-biologist").update(is_active=False)
        self.card()
        self.card(hero="nis-biologist")
        restore_character_sets()
        client = self.account_client()
        response = client.get(reverse("tower:home"), {"hero": "nis-coder"})
        self.assertContains(response, 'data-set-received="true"')
        self.assertContains(response, "companion-placeholder.")
        response = client.get(reverse("tower:home"), {"hero": "nis-biologist"})
        self.assertTrue(response.context["hero_detail"]["set_pending"])
        shop = client.get(reverse("tower:home"), {"tab": "avatar"})
        self.assertEqual(len(shop.context["cosmetic_items"]), 7)

    def test_publication_is_repeatable_preserves_pause_and_rejects_modified_snapshot(self):
        entry = next(row for row in load_character_sets().sets if row.hero_id == "nis-coder")
        profile = next(row for row in load_behavior_profiles().profiles if row.hero_id == "nis-coder")
        before = CharacterSetVersion.objects.count()
        publish_character_set(entry, profile)
        self.assertEqual(CharacterSetVersion.objects.count(), before)
        CharacterSetPublication.objects.filter(hero_id="nis-coder").update(is_active=False)
        CosmeticDefinition.objects.filter(pk="nis-coder-head").update(is_active=False)
        publish_character_set(entry, profile)
        self.assertFalse(CharacterSetPublication.objects.get(hero_id="nis-coder").is_active)
        self.assertFalse(CosmeticDefinition.objects.get(pk="nis-coder-head").is_active)
        changed = entry.model_dump()
        changed["components"]["head"]["labels"]["ru"] = "Changed"
        with self.assertRaises(ValidationError):
            publish_character_set(type(entry).model_validate(changed), profile)

    def test_frozen_migration_restores_existing_cards_without_live_files_or_new_wallet_entries(self):
        self.award(amount=50)
        card = self.card()
        migration = importlib.import_module("tower.migrations.0006_publish_initial_character_sets")
        apps = MigrationExecutor(connection).loader.project_state().apps
        before = WalletEntry.objects.count()
        with patch("pathlib.Path.read_text", side_effect=AssertionError("Migration must be frozen")):
            migration.seed_and_restore(apps, SimpleNamespace(connection=connection))
            migration.seed_and_restore(apps, SimpleNamespace(connection=connection))
        self.assertEqual(PlayerCosmetic.objects.count(), 6)
        self.assertEqual(PlayerCharacterSet.objects.count(), 1)
        self.assertEqual(CharacterSetEntitlement.objects.get().earned_at, card.assembled_at)
        self.assertEqual(WalletEntry.objects.count(), before)

    def test_initial_migration_preserves_newer_publication_and_deliberate_pause(self):
        original = CharacterSetVersion.objects.get(slug="nis-coder-v1")
        config = copy.deepcopy(original.config)
        config["revision"] = 2
        for component in config["components"].values():
            component["asset"]["path"] = component["asset"]["path"].replace("/v1/", "/v2/")
        newer = CharacterSetVersion.objects.create(slug="nis-coder-v2", hero=original.hero,
            revision=2, config=config, behavior=original.behavior, asset_manifest=[])
        CharacterSetPublication.objects.filter(hero=original.hero).update(version=newer, is_active=False)
        migration = importlib.import_module("tower.migrations.0006_publish_initial_character_sets")
        apps = MigrationExecutor(connection).loader.project_state().apps
        migration.seed_and_restore(apps, SimpleNamespace(connection=connection))
        publication = CharacterSetPublication.objects.get(hero=original.hero)
        self.assertEqual(publication.version_id, newer.pk)
        self.assertFalse(publication.is_active)

    def test_remaining_set_migration_backfills_old_collection_without_spending_or_file_dependencies(self):
        self.award(amount=100)
        buy_cosmetic(student=self.student,cosmetic_id="cap",request_id=uuid.uuid4())
        save_avatar(student=self.student,shape="triangle",color="#10b981",accessories={"head":"cap","glasses":"","mustache":""},theme="midnight")
        self.card(hero="nis-biologist")
        before=WalletEntry.objects.count()
        migration=importlib.import_module("tower.migrations.0010_publish_remaining_character_sets")
        apps=MigrationExecutor(connection).loader.project_state().apps
        with patch("pathlib.Path.read_text",side_effect=AssertionError("Migration must be frozen")):
            migration.seed_and_restore(apps,SimpleNamespace(connection=connection))
            migration.seed_and_restore(apps,SimpleNamespace(connection=connection))
        self.assertEqual((CharacterSetPublication.objects.count(),PlayerCharacterSet.objects.count(),PlayerCosmetic.objects.count()),(12,1,7))
        self.assertEqual(WalletEntry.objects.count(),before)
        player=TowerPlayer.objects.get(student=self.student)
        self.assertEqual(player.avatar_accessories,{"head":"cap"})

    def test_release_readiness_checks_assets_and_database_without_openai(self):
        with patch("core.ai_assist._get_openai_client",side_effect=AssertionError("No API calls")):
            call_command("check_companion_readiness",class_id=self.group.pk,stdout=io.StringIO())


@skipUnless(connection.vendor == "postgresql", "Set grant races require PostgreSQL row locks")
@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CharacterSetConcurrencyTests(WalletFixtures, TransactionTestCase):
    parallel = wallet_races.WalletConcurrencyTests.parallel

    def setUp(self):
        self.create_accounts()
        call_command("seed_tower_catalog", stdout=io.StringIO())
        call_command("publish_character_sets", stdout=io.StringIO())
        self.award(amount=100)

    def test_simultaneous_restore_grants_one_complete_set(self):
        player = TowerPlayer.objects.get()
        card = PlayerHero.objects.create(player=player, hero_id="nis-coder", fragments=10, assembled_at=timezone.now())
        result = self.parallel(lambda: grant_character_set(player=player, hero=card.hero),
                               lambda: grant_character_set(player=player, hero=card.hero))
        self.assertTrue(all(kind == "ok" for kind, _ in result))
        self.assertEqual(sorted(row["newly_granted"] for _, row in result), [False, True])
        self.assertEqual((CharacterSetEntitlement.objects.count(), PlayerCharacterSet.objects.count(), PlayerCosmetic.objects.count()), (1, 1, 6))
        self.assertEqual(Wallet.objects.get().niscoin, 100)

    @patch("tower.services.collection._draw", return_value=("nis-coder", "common"))
    def test_concurrent_pack_assemblies_have_one_entitlement_and_six_items(self, draw):
        PlayerHero.objects.create(player=TowerPlayer.objects.get(), hero_id="nis-coder", fragments=8)
        def open_one():
            return open_pack(student=self.student, offer="school", version="school-v1", request_id=uuid.uuid4())
        result = self.parallel(open_one, open_one)
        self.assertTrue(all(kind == "ok" for kind, _ in result))
        self.assertEqual((PackOpening.objects.count(), PlayerCharacterSet.objects.count(), PlayerCosmetic.objects.count()), (2, 1, 6))
        self.assertEqual(Wallet.objects.get().niscoin, 0)
