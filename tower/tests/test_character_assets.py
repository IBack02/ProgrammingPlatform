import copy
import io
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from pydantic import ValidationError

from tower.character_assets import ASSET_ROOT, PREVIEW_HEROES, inspect_asset, release_manifest
from tower.character_contracts import Asset, BehaviorCatalog, CharacterSetCatalog, load_behavior_profiles, load_character_sets
from tower.companion_personalities import behavior_instructions
from tower.models import PlayerCosmetic, TowerPlayer
from .wallet_fixtures import WalletFixtures


class CharacterArtTests(SimpleTestCase):
    def test_two_complete_pilot_sets_have_verified_files_including_supplied_chibi(self):
        manifest = release_manifest()
        self.assertEqual({entry["hero_id"] for entry in manifest["sets"]}, set(PREVIEW_HEROES))
        self.assertEqual(sum(len(entry["assets"]) for entry in manifest["sets"]), 14)
        for entry in manifest["sets"]:
            for asset in entry["assets"]:
                self.assertGreater(asset["bytes"], 100)
                self.assertEqual(len(asset["sha256"]), 64)
                self.assertFalse(asset["placeholder"])

    def test_all_twelve_helpers_use_supplied_transparent_pngs(self):
        for entry in load_character_sets().sets:
            component = entry.components["companion"]
            self.assertFalse(component.placeholder)
            self.assertTrue(component.asset.path.endswith('/companion.png'))
            inspect_asset(component.asset, require_alpha=True)

    def test_placeholder_is_explicit_and_cannot_impersonate_finished_character_art(self):
        data = load_character_sets().model_dump()
        data["sets"][0]["components"]["companion"]["asset"] = {
            "path": "sets/nis-coder/v2/companion-placeholder.svg", "width": 512, "height": 512}
        data["sets"][0]["components"]["companion"]["placeholder"] = False
        with self.assertRaises(ValidationError):
            CharacterSetCatalog.model_validate(data)
        data = load_character_sets().model_dump()
        data["sets"][0]["components"]["head"]["placeholder"] = True
        with self.assertRaises(ValidationError):
            CharacterSetCatalog.model_validate(data)

    def test_archive_preserves_original_bytes_and_contains_manifest(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "assets.zip"
            call_command("prepare_character_assets", archive=path, stdout=io.StringIO())
            with ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(len(archive.namelist()), 15)
                for entry in manifest["sets"]:
                    for asset in entry["assets"]:
                        self.assertEqual(archive.read(asset["path"]), (ASSET_ROOT / asset["path"]).read_bytes())

    def test_declared_dimensions_and_active_svg_content_are_checked(self):
        asset = Asset(path="sets/nis-coder/v1/ui-loader.svg", width=128, height=128)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / asset.path
            path.parent.mkdir(parents=True)
            for content in (
                '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128"><script>alert(1)</script></svg>',
                '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128"><path onclick="alert(1)"/></svg>',
                '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 1 1"/>',
            ):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    inspect_asset(asset, root=root)

    def test_every_character_has_distinct_style_and_three_localized_examples(self):
        catalog = load_behavior_profiles()
        catalog.check_references(load_character_sets())
        self.assertEqual(len(catalog.profiles), 12)
        self.assertEqual(len({profile.temperament for profile in catalog.profiles}), 12)
        for profile in catalog.profiles:
            for language, examples in profile.examples.items():
                self.assertEqual(len(examples), 3)
                prompt = behavior_instructions(profile.hero_id, language)
                self.assertIn(profile.tone, prompt)
                self.assertIn(examples[0], prompt)
                self.assertIn("600", prompt)
                self.assertIn("Примеры показывают только манеру речи", prompt)
        with self.assertRaises(ValueError):
            behavior_instructions("unknown")
        with self.assertRaises(ValueError):
            behavior_instructions("nis-coder", "de")

    def test_missing_language_profile_or_duplicate_profile_is_rejected(self):
        original = load_behavior_profiles().model_dump()
        duplicate = copy.deepcopy(original)
        duplicate["profiles"].append(copy.deepcopy(duplicate["profiles"][0]))
        missing_language = copy.deepcopy(original)
        del missing_language["profiles"][0]["examples"]["kk"]
        for data in (duplicate, missing_language):
            with self.assertRaises(ValidationError):
                BehaviorCatalog.model_validate(data)
        original["profiles"].pop()
        with self.assertRaises(ValueError):
            BehaviorCatalog.model_validate(original).check_references(load_character_sets())

    def test_chibi_dry_run_with_empty_input_never_generates_or_rewrites_catalog(self):
        from tower.character_contracts import CONTENT_DIR
        manifest = CONTENT_DIR / "character_sets.json"
        before = manifest.read_bytes()
        with TemporaryDirectory() as directory, patch("core.ai_assist._get_openai_client", side_effect=AssertionError("No API")):
            output = io.StringIO()
            call_command("import_tower_chibi", directory=Path(directory), dry_run=True, stdout=output)
        self.assertIn("0 chibi", output.getvalue())
        self.assertEqual(before, manifest.read_bytes())

    def test_mismatched_svg_headwear_is_not_accepted(self):
        data = load_character_sets().model_dump()
        data["sets"][0]["components"]["head"]["asset"]["path"] = "sets/nis-coder/v1/head.svg"
        with self.assertRaises(ValidationError):
            CharacterSetCatalog.model_validate(data)

    def test_import_preserves_pixels_creates_new_revision_and_is_idempotent(self):
        from tower.management.commands import import_tower_chibi as command
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assets, config, incoming = root / "assets", root / "config", root / "incoming"
            config.mkdir()
            incoming.mkdir()
            shutil.copytree(ASSET_ROOT / "sets" / "nis-coder", assets / "sets" / "nis-coder")
            source = ASSET_ROOT / "sets" / "nis-coder" / "v1" / "head.png"
            # Reuse an existing alpha image as a byte-preservation test fixture.
            shutil.copyfile(source, incoming / "nis-coder.png")
            manifest = config / "character_sets.json"
            manifest.write_text(load_character_sets().model_dump_json(), encoding="utf-8")
            reader = lambda: CharacterSetCatalog.model_validate_json(manifest.read_text(encoding="utf-8"))
            with patch.object(command, "ASSET_ROOT", assets), patch.object(command, "CONTENT_DIR", config), patch.object(command, "load_character_sets", side_effect=reader):
                call_command("import_tower_chibi", directory=incoming, stdout=io.StringIO())
                updated = reader()
                entry = next(row for row in updated.sets if row.hero_id == "nis-coder")
                self.assertEqual((entry.revision, entry.status, entry.components["companion"].placeholder), (3, "draft", False))
                destination = assets / entry.components["companion"].asset.path
                self.assertEqual(destination.read_bytes(), source.read_bytes())
                before = manifest.read_bytes()
                output = io.StringIO()
                call_command("import_tower_chibi", directory=incoming, stdout=output)
                self.assertIn("0 chibi", output.getvalue())
                self.assertEqual(before, manifest.read_bytes())
                self.assertTrue((assets / "sets/nis-coder/v1/companion-placeholder.svg").is_file())


@override_settings(TOWER_ENABLED=True, STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SetArtPreviewTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()

    def test_teacher_can_review_mixed_art_without_player_or_inventory_mutations(self):
        url = reverse("tower:set_preview")
        response = self.account_client(self.teacher).get(url, {"head": "nis-artist", "prop": "nis-coder", "size": 46})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-avatar-shape=", count=25)
        self.assertContains(response, "head.png")
        self.assertContains(response, "companion.png")
        self.assertFalse(TowerPlayer.objects.exists())
        self.assertFalse(PlayerCosmetic.objects.exists())

    def test_student_anonymous_and_revoked_teachers_cannot_use_internal_preview(self):
        url = reverse("tower:set_preview")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.account_client().get(url).status_code, 403)
        self.teacher.can_manage_tower = False
        self.teacher.save(update_fields=["can_manage_tower"])
        self.assertEqual(self.account_client(self.teacher).get(url).status_code, 403)

    def test_invalid_preview_fields_fail_and_disabled_feature_revokes_access(self):
        client = self.account_client(self.teacher)
        url = reverse("tower:set_preview")
        self.assertEqual(client.get(url, {"head": "../secret"}).status_code, 400)
        self.assertEqual(client.get(url, {"size": "nan"}).status_code, 400)
        with override_settings(TOWER_ENABLED=False):
            self.assertEqual(client.get(url).status_code, 403)
