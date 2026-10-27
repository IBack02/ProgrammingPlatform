import copy
import io
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree

from django.core.management import call_command, CommandError
from django.forms.models import model_to_dict
from django.test import SimpleTestCase, TestCase, override_settings
from pydantic import ValidationError

from tower.avatar_geometry import place_attachment
from tower.character_contracts import (
    CONTENT_DIR, AppearanceSelection, Asset, Attachment, AvatarGeometry,
    CharacterSetCatalog, contract_schemas, load_avatar_geometry, load_character_sets,
)
from tower.companion_contracts import (
    COOLDOWN, CompanionStatus, SourceCandidate, SourceKind, account_availability,
    choose_source, feedback_revision,
)
from tower.models import PlayerCosmetic, PlayerHero, TowerPlayer, Wallet, WalletEntry
from tower.services.collection import buy_cosmetic, save_avatar
from .wallet_fixtures import WalletFixtures


class CharacterCatalogContractTests(SimpleTestCase):
    def catalog_data(self):
        return json.loads((CONTENT_DIR / "character_sets.json").read_text(encoding="utf-8"))

    def test_all_cards_have_complete_published_definitions(self):
        catalog = load_character_sets()
        cards = json.loads((CONTENT_DIR / "cards.json").read_text(encoding="utf-8"))
        economy = json.loads((CONTENT_DIR / "economy.json").read_text(encoding="utf-8"))
        catalog.check_references((card["id"] for card in cards["cards"]),
                                 (item["slug"] for item in economy["cosmetics"]))
        self.assertEqual(len(catalog.sets), 12)
        self.assertEqual(sum(len(entry.components) for entry in catalog.sets), 72)
        self.assertEqual({entry.hero_id for entry in catalog.published_sets()}, {card["id"] for card in cards["cards"]})

    def test_missing_component_or_duplicate_character_cannot_be_published(self):
        missing = self.catalog_data()
        del missing["sets"][0]["components"]["background"]
        duplicate = self.catalog_data()
        duplicate["sets"].append(copy.deepcopy(duplicate["sets"][0]))
        for data in (missing, duplicate):
            with self.subTest(case=data["sets"][0]["hero_id"]), self.assertRaises(ValidationError):
                CharacterSetCatalog.model_validate(data)

    def test_unknown_versions_and_extra_fields_do_not_silently_change_contract(self):
        for version in (True, 1.0, "1", 2):
            data = self.catalog_data()
            data["schema_version"] = version
            with self.subTest(version=version), self.assertRaises(ValidationError):
                CharacterSetCatalog.model_validate(data)
        data = self.catalog_data()
        data["sets"][0]["components"]["companion"]["api_key"] = "unrecognized-field"
        with self.assertRaises(ValidationError):
            CharacterSetCatalog.model_validate(data)

    def test_cross_character_asset_slot_profile_and_revision_are_rejected(self):
        for slot, field, value in (
            ("head", "id", "nis-chemist-head"),
            ("head", "slot", "prop"),
            ("companion", "behavior_profile", "nis-chemist"),
            ("head", "asset", {"path": "sets/nis-coder/v2/head.webp", "width": 512, "height": 512}),
        ):
            data = self.catalog_data()
            data["sets"][0]["components"][slot][field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                CharacterSetCatalog.model_validate(data)

    def test_catalog_cannot_omit_a_hero_or_replace_preexisting_purchase(self):
        catalog = load_character_sets()
        heroes = [entry.hero_id for entry in catalog.sets]
        with self.assertRaises(ValueError):
            catalog.check_references(heroes[:-1])
        with self.assertRaises(ValueError):
            catalog.check_references(heroes, [catalog.sets[0].components["head"].id])

    def test_paths_stay_relative_and_inside_a_versioned_set_directory(self):
        for path in ("https://cdn.example.org/x.webp", "sets/nis-coder/v1/../head.webp",
                     "/sets/nis-coder/v1/head.webp", "sets\\nis-coder\\v1\\head.webp",
                     "sets/nis-coder/v1/head.gif", "sets/nis-coder/v1/head.webp?token=secret"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                Asset(path=path, width=512, height=512)

    def test_only_avatar_items_have_anchors_and_only_companions_have_behavior(self):
        for slot, field, value in (
            ("head", "attachment", None),
            ("background", "attachment", self.catalog_data()["sets"][0]["components"]["head"]["attachment"]),
            ("head", "behavior_profile", "nis-coder"),
            ("background", "animation", "rotate"),
        ):
            data = self.catalog_data()
            data["sets"][0]["components"][slot][field] = value
            with self.subTest(slot=slot, field=field), self.assertRaises(ValidationError):
                CharacterSetCatalog.model_validate(data)

    def test_schema_export_and_validation_need_no_database_or_openai(self):
        with patch("core.ai_assist._get_openai_client", side_effect=AssertionError("No API calls at stage 1")):
            output = io.StringIO()
            call_command("check_character_sets", schema=True, stdout=output)
        schema = json.loads(output.getvalue())
        self.assertIn("CharacterSetCatalog", schema)
        self.assertEqual(schema["companion_policy"]["cooldown_seconds"], 1800)
        self.assertEqual(schema["companion_policy"]["scope"], "account")
        self.assertEqual(schema["companion_policy"]["sentences"], [2, 4])
        self.assertEqual(schema["companion_policy"]["source_priority"],
                         ["unfinished", "teacher_feedback", "lesson_fact"])
        self.assertEqual(schema["AppearanceSelection"], contract_schemas()["AppearanceSelection"])

    def test_drafts_fail_the_publication_gate(self):
        from unittest.mock import patch
        data = self.catalog_data()
        data["sets"][0]["status"] = "draft"
        with patch("tower.management.commands.check_character_sets.load_character_sets", return_value=CharacterSetCatalog.model_validate(data)), self.assertRaisesMessage(CommandError, "drafts"):
            call_command("check_character_sets", require_published=True, stdout=io.StringIO())

    def test_preview_exercises_all_five_shapes_and_three_prop_positions(self):
        with TemporaryDirectory() as directory:
            preview = Path(directory) / "geometry.svg"
            call_command("check_character_sets", preview=preview, stdout=io.StringIO())
            root = ElementTree.fromstring(preview.read_text(encoding="utf-8"))
        pairs = {(node.attrib["data-shape"], node.attrib["data-anchor"])
                 for node in root.iter() if "data-shape" in node.attrib}
        self.assertEqual(len(pairs), 15)
        self.assertEqual({shape for shape, _ in pairs}, set(load_avatar_geometry().shapes))
        self.assertEqual({anchor for _, anchor in pairs}, {"hand", "side", "ground"})


class AvatarAttachmentTests(SimpleTestCase):
    def test_pivot_lands_on_anchor_at_every_size_shape_rotation_and_aspect(self):
        geometry = load_avatar_geometry()
        for shape in geometry.shapes:
            for anchor in ("head", "hand", "side", "ground"):
                for size in (24, 46, 110, 320):
                    for width, height in ((512, 512), (512, 256), (128, 512)):
                        with self.subTest(shape=shape, anchor=anchor, size=size, aspect=(width, height)):
                            asset = Asset(path="sets/nis-coder/v1/head.webp", width=width, height=height)
                            attachment = Attachment(anchor=anchor, pivot={"x": 0.17, "y": 0.83},
                                                    width_ratio=1.12, rotation=37.0)
                            placement = place_attachment(geometry=geometry, shape=shape, asset=asset,
                                                         attachment=attachment, size=size)
                            target = geometry.shapes[shape].anchors[anchor].point
                            x, y = placement.map_point(width * 0.17, height * 0.83)
                            self.assertAlmostEqual(x, target.x * size)
                            self.assertAlmostEqual(y, target.y * size)
                            horizontal = math.hypot(placement.a * width, placement.b * width)
                            vertical = math.hypot(placement.c * height, placement.d * height)
                            self.assertAlmostEqual(horizontal / vertical, width / height)

    def test_known_quarter_turn_and_behind_layer(self):
        geometry = load_avatar_geometry()
        asset = Asset(path="sets/nis-coder/v1/prop.webp", width=100, height=200)
        attachment = Attachment(anchor="head", pivot={"x": 0.0, "y": 0.0}, width_ratio=1.0,
                                rotation=90.0, layer="behind")
        placement = place_attachment(geometry=geometry, shape="square", asset=asset, attachment=attachment, size=100)
        self.assertEqual(placement.layer, "behind")
        self.assertAlmostEqual(placement.map_point(0, 0)[0], 50)
        self.assertAlmostEqual(placement.map_point(0, 0)[1], 4)
        self.assertAlmostEqual(placement.map_point(100, 0)[1], 104)
        self.assertAlmostEqual(placement.map_point(0, 200)[0], -150)

    def test_invalid_numbers_sizes_and_missing_shape_are_rejected(self):
        for value in (True, float("nan"), float("inf"), -0.1, 1.1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Attachment(anchor="head", pivot={"x": value, "y": 0.5}, width_ratio=1.0)
        geometry = load_avatar_geometry()
        asset = Asset(path="sets/nis-coder/v1/head.webp", width=512, height=512)
        attachment = Attachment(anchor="head", pivot={"x": 0.5, "y": 0.5}, width_ratio=1.0)
        for size in (0, -1, True, "46", float("inf"), float("nan")):
            with self.subTest(size=size), self.assertRaises(ValueError):
                place_attachment(geometry=geometry, shape="square", asset=asset, attachment=attachment, size=size)
        with self.assertRaises(ValueError):
            place_attachment(geometry=geometry, shape="unknown", asset=asset, attachment=attachment, size=46)
        data = geometry.model_dump()
        del data["shapes"]["triangle"]
        with self.assertRaises(ValidationError):
            AvatarGeometry.model_validate(data)

    def test_new_components_mix_independently_with_legacy_slots(self):
        selection = AppearanceSelection(slots={"head": "cap", "glasses": "round-glasses",
            "prop": "nis-coder-prop", "background": "nis-biologist-background",
            "ui_loader": "nis-chemist-ui-loader", "ui_decoration": "nis-musician-ui-decoration",
            "companion": "nis-astronomer-companion"}, legacy_theme="forest")
        owned = {item: slot for slot, item in selection.slots.items()}
        owned["forest"] = "theme"
        selection.check_ownership(owned)
        with self.assertRaises(ValueError):
            selection.check_ownership({**owned, "cap": "prop"})
        with self.assertRaises(ValueError):
            selection.check_ownership({key: value for key, value in owned.items() if key != "nis-coder-prop"})
        with self.assertRaises(ValidationError):
            AppearanceSelection(slots={"injected_slot": "cap"})


class CompanionRuleTests(SimpleTestCase):
    now = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)

    def state(self, **kwargs):
        return account_availability(**{"now": self.now, "enabled": True,
            "companion_selected": True, "has_source": True, **kwargs})

    def test_notification_requires_selected_helper_source_and_available_account(self):
        self.assertTrue(self.state().show_notification)
        self.assertEqual(self.state(has_source=False).status, CompanionStatus.EMPTY)
        for settings in ({"enabled": False}, {"companion_selected": False}, {"has_source": False}):
            with self.subTest(settings=settings):
                self.assertFalse(self.state(**settings).show_notification)

    def test_exactly_thirty_minutes_after_success_across_timezone_offsets(self):
        success = self.now.astimezone(timezone(timedelta(hours=5)))
        pending = self.state(now=self.now + COOLDOWN - timedelta(microseconds=1), last_success_at=success)
        self.assertEqual(pending.status, CompanionStatus.COOLDOWN)
        self.assertEqual(pending.next_available_at, success + COOLDOWN)
        self.assertFalse(pending.show_notification)
        self.assertEqual(self.state(now=self.now + COOLDOWN, last_success_at=success).status, CompanionStatus.READY)

    def test_switching_helper_does_not_change_account_cooldown(self):
        kwargs = {"last_success_at": self.now - timedelta(minutes=2)}
        self.assertEqual(self.state(**kwargs).status, CompanionStatus.COOLDOWN)
        self.assertEqual(self.state(companion_selected=False, **kwargs).status, CompanionStatus.INACTIVE)
        self.assertEqual(self.state(companion_selected=True, **kwargs).next_available_at,
                         self.now + timedelta(minutes=28))

    def test_inflight_request_and_failure_retry_are_distinct_from_success(self):
        self.assertEqual(self.state(reservation_expires_at=self.now + timedelta(seconds=20)).status,
                         CompanionStatus.GENERATING)
        self.assertEqual(self.state(reservation_expires_at=self.now).status, CompanionStatus.READY)
        retry = self.now + timedelta(seconds=30)
        self.assertEqual(self.state(retry_after=retry).status, CompanionStatus.RETRY_WAIT)
        self.assertEqual(self.state(retry_after=retry).next_available_at, retry)
        self.assertEqual(self.state(now=retry, retry_after=retry).status, CompanionStatus.READY)

    def test_naive_timestamps_do_not_silently_use_server_local_time(self):
        with self.assertRaises(ValueError):
            self.state(last_success_at=self.now.replace(tzinfo=None))

    def test_unfinished_work_always_precedes_feedback_and_facts(self):
        fact = SourceCandidate(SourceKind.LESSON_FACT, 3, "v1")
        feedback = SourceCandidate(SourceKind.TEACHER_FEEDBACK, 2, "comment-v1")
        work = SourceCandidate(SourceKind.UNFINISHED, 1, "v1", self.now)
        self.assertIs(choose_source(iter([fact, feedback, work])), work)
        self.assertIs(choose_source(iter([fact, feedback])), feedback)
        self.assertIs(choose_source([fact, feedback], considered_feedback={feedback.identity}), fact)

    def test_work_is_rotated_inside_its_priority(self):
        recent = SourceCandidate(SourceKind.UNFINISHED, 1, "v1", self.now)
        earlier = SourceCandidate(SourceKind.UNFINISHED, 2, "v1", self.now - timedelta(hours=2))
        unseen = SourceCandidate(SourceKind.UNFINISHED, 3, "v1")
        self.assertIs(choose_source([recent, earlier, unseen]), unseen)
        self.assertIs(choose_source([recent, earlier]), earlier)

    def test_feedback_history_is_per_resource_and_version_not_character(self):
        first = SourceCandidate(SourceKind.TEACHER_FEEDBACK, 42, feedback_revision("Повтори цикл"))
        same = SourceCandidate(SourceKind.TEACHER_FEEDBACK, 42, feedback_revision("  Повтори\nцикл  "))
        edited = SourceCandidate(SourceKind.TEACHER_FEEDBACK, 42, feedback_revision("Проверь границы цикла"))
        other_answer = SourceCandidate(SourceKind.TEACHER_FEEDBACK, 43, first.revision)
        self.assertIsNone(choose_source([same], considered_feedback={first.identity}))
        self.assertIs(choose_source([edited], considered_feedback={first.identity}), edited)
        self.assertIs(choose_source([other_answer], considered_feedback={first.identity}), other_answer)


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ExistingCollectionCompatibilityTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()
        call_command("seed_tower_catalog", stdout=io.StringIO())

    def test_actual_purchases_and_saved_avatar_survive_contract_validation(self):
        import uuid

        self.award(amount=200)
        for item in ("cap", "round-glasses", "forest"):
            buy_cosmetic(student=self.student, cosmetic_id=item, request_id=uuid.uuid4())
        player = save_avatar(student=self.student, shape="triangle", color="#10b981",
            accessories={"head": "cap", "glasses": "round-glasses", "mustache": ""}, theme="forest")
        before = model_to_dict(player)
        selection = AppearanceSelection.from_legacy(shape=player.avatar_shape, color=player.avatar_color,
            accessories=player.avatar_accessories, theme=player.profile_theme)
        selection.check_ownership(dict(PlayerCosmetic.objects.filter(player=player).values_list("cosmetic_id", "cosmetic__slot")))
        call_command("check_character_sets", stdout=io.StringIO())
        player.refresh_from_db()
        self.assertEqual(model_to_dict(player), before)
        self.assertEqual(selection.slots, {"head": "cap", "glasses": "round-glasses"})
        self.assertEqual(selection.legacy_theme, "forest")
        self.assertEqual(Wallet.objects.get().niscoin, 30)
        self.assertEqual((PlayerCosmetic.objects.count(), WalletEntry.objects.count()), (3, 4))

    def test_validation_does_not_award_new_items_to_previously_assembled_cards(self):
        from django.utils import timezone as django_timezone

        self.award(amount=50)
        player = TowerPlayer.objects.get()
        hero = PlayerHero.objects.create(player=player, hero_id="nis-coder", fragments=10,
                                         assembled_at=django_timezone.now())
        before = model_to_dict(hero)
        call_command("check_character_sets", stdout=io.StringIO())
        hero.refresh_from_db()
        self.assertEqual(model_to_dict(hero), before)
        self.assertFalse(PlayerCosmetic.objects.exists())
        self.assertEqual(Wallet.objects.get().niscoin, 50)
