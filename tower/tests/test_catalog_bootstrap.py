import copy
import importlib
import uuid
from types import SimpleNamespace

from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, override_settings
from django.urls import reverse

from tower.models import CosmeticDefinition, HeroDefinition, PackOffer, PackOpening, PackVersion, TowerPlayer, Wallet
from .wallet_fixtures import WalletFixtures


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CatalogBootstrapTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()

    def setUp(self):
        # Remove later catalog publications using their historical managers before
        # reproducing the older, genuinely empty catalog installation.
        current = MigrationExecutor(connection).loader.project_state().apps
        current.get_model("tower", "CosmeticDefinition").objects.filter(acquisition="character_set").delete()
        for name in ("CharacterSetPublication", "CharacterSetVersion"):
            current.get_model("tower", name).objects.all().delete()
        self.apps = current
        self.migration = importlib.import_module("tower.migrations.0004_seed_initial_catalog")
        # Use historical models matching the test database's current schema.
        # Fresh migrations also exercise this function against its original schema.
        for name in ("PackOffer", "PackVersion", "HeroDefinition", "CosmeticDefinition"):
            self.apps.get_model("tower", name).objects.all().delete()

    def bootstrap(self):
        self.migration.seed_initial_catalog(self.apps, SimpleNamespace(connection=connection))

    def test_initial_migration_seeds_catalogue_without_creating_players_or_money(self):
        self.bootstrap()
        self.bootstrap()
        self.assertEqual((HeroDefinition.objects.count(), PackOffer.objects.count(), CosmeticDefinition.objects.count()), (12, 1, 7))
        self.assertEqual(PackOffer.objects.get().version.config["price"], 50)
        self.assertFalse(TowerPlayer.objects.exists())
        self.assertFalse(Wallet.objects.exists())
        self.assertFalse(PackOpening.objects.exists())

    def test_bootstrap_recovers_the_reported_empty_journal_and_makes_pack_purchasable(self):
        self.award(amount=50)
        client = self.account_client()
        before = client.get(reverse("tower:home"))
        self.assertEqual(len(before.context["cards"]), 0)
        self.bootstrap()
        after = client.get(reverse("tower:home"))
        self.assertContains(after, 'data-collection-state="undiscovered"', count=12)
        self.assertEqual(Wallet.objects.get().niscoin, 50)
        packs = client.get(reverse("tower:home"), {"tab": "packs"})
        self.assertEqual(packs.context["pack"]["price"], 50)
        response = client.post(reverse("tower:student_action", args=["pack"]), {
            "offer": "school", "version": "school-v1", "request_id": uuid.uuid4(),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Wallet.objects.get().niscoin, 0)
        self.assertEqual(PackOpening.objects.count(), 1)

    def test_existing_shutdowns_and_newer_offer_version_are_preserved(self):
        self.bootstrap()
        config = copy.deepcopy(PackVersion.objects.get().config)
        config["price"] = 75
        newer = PackVersion.objects.create(slug="school-v2", config=config)
        PackOffer.objects.filter(pk="school").update(version=newer, is_active=False)
        HeroDefinition.objects.filter(pk="nis-coder").update(is_active=False)
        CosmeticDefinition.objects.filter(pk="cap").update(is_active=False)
        self.award(amount=50)
        self.bootstrap()
        offer = PackOffer.objects.get()
        self.assertEqual(offer.version_id, newer.pk)
        self.assertFalse(offer.is_active)
        self.assertFalse(HeroDefinition.objects.get(pk="nis-coder").is_active)
        self.assertFalse(CosmeticDefinition.objects.get(pk="cap").is_active)
        self.assertEqual(Wallet.objects.get().niscoin, 50)

    def test_migration_does_not_read_changing_content_manifests(self):
        from unittest.mock import patch
        with patch("pathlib.Path.read_text", side_effect=AssertionError("must use frozen data")):
            self.bootstrap()
        self.assertEqual(HeroDefinition.objects.count(), 12)

    def test_conflicting_published_version_is_not_overwritten(self):
        version = PackVersion.objects.create(slug="school-v1", config={"price": 999})
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.bootstrap()
        self.assertEqual(PackVersion.objects.get(pk=version.pk).config, {"price": 999})
        self.assertFalse(HeroDefinition.objects.exists())
