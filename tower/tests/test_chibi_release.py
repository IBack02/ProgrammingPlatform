import importlib
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, override_settings
from django.utils import timezone

from tower.models import (CharacterSetPublication, CharacterSetVersion, CosmeticDefinition, PlayerCharacterSet,
                          PlayerCosmetic, PlayerHero, TowerPlayer, WalletEntry)
from tower.services.appearance import SELECTION_SLOTS, appearance_context, save_appearance
from tower.services.character_sets import grant_character_set
from .wallet_fixtures import WalletFixtures


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ChibiReleaseTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()

    def test_old_inventory_and_selection_gain_real_art_without_new_rewards_or_spending(self):
        self.award(amount=100)
        player = TowerPlayer.objects.get()
        card = PlayerHero.objects.create(player=player, hero_id="nis-coder", fragments=10, assembled_at=timezone.now())
        grant_character_set(player=player, hero=card.hero)
        old = CharacterSetVersion.objects.get(slug="nis-coder-v1")
        old_config = old.config
        CharacterSetPublication.objects.filter(hero=card.hero).update(version=old)
        for component in old.config["components"].values():
            CosmeticDefinition.objects.filter(pk=component["id"]).update(set_version=old, appearance=component)
        selections = dict.fromkeys(SELECTION_SLOTS, "")
        selections["companion"] = "nis-coder-companion"
        save_appearance(student=self.student, shape="square", color="#3b82f6", theme="midnight", selections=selections)
        inventory = set(PlayerCosmetic.objects.values_list("id", flat=True))
        entries = WalletEntry.objects.count()
        apps = MigrationExecutor(connection).loader.project_state().apps
        migrate = importlib.import_module("tower.migrations.0013_publish_chibi_art").publish_chibi
        with patch("pathlib.Path.read_text", side_effect=AssertionError("Frozen migrations must not read manifests")):
            migrate(apps, SimpleNamespace(connection=connection))
            migrate(apps, SimpleNamespace(connection=connection))
        player.refresh_from_db()
        item = appearance_context(player)["selected"]["companion"]
        self.assertEqual(item["id"], "nis-coder-companion")
        self.assertFalse(item["placeholder"])
        self.assertIn("/v2/companion", item["url"])
        self.assertEqual(player.appearance, {"companion": "nis-coder-companion"})
        self.assertEqual(set(PlayerCosmetic.objects.values_list("id", flat=True)), inventory)
        self.assertEqual(PlayerCharacterSet.objects.count(), 1)
        self.assertEqual(WalletEntry.objects.count(), entries)
        self.assertEqual(CharacterSetVersion.objects.get(pk=old.pk).config, old_config)

    def test_chibi_migration_preserves_a_newer_paused_publication(self):
        import copy
        old = CharacterSetVersion.objects.get(slug="nis-coder-v2")
        config = copy.deepcopy(old.config)
        config["revision"] = 3
        for component in config["components"].values():
            component["asset"]["path"] = component["asset"]["path"].replace("/v2/", "/v3/")
        newer = CharacterSetVersion.objects.create(slug="nis-coder-v3", hero=old.hero, revision=3,
            config=config, behavior=old.behavior, asset_manifest=[])
        CharacterSetPublication.objects.filter(hero=old.hero).update(version=newer, is_active=False)
        apps = MigrationExecutor(connection).loader.project_state().apps
        importlib.import_module("tower.migrations.0013_publish_chibi_art").publish_chibi(apps, SimpleNamespace(connection=connection))
        publication = CharacterSetPublication.objects.get(hero=old.hero)
        self.assertEqual(publication.version_id, newer.pk)
        self.assertFalse(publication.is_active)
