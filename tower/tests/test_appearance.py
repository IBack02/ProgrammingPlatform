import copy
import io
import uuid

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.game_views import _participant_row
from core.models import GameModule, GameParticipant, GameRound, Session
from tower.models import PlayerHero, TowerPlayer, Wallet, WalletEntry
from tower.services.appearance import SELECTION_SLOTS, appearance_context, save_appearance
from tower.services.avatar import saved_avatar
from tower.services.character_sets import grant_character_set
from tower.services.collection import buy_cosmetic
from tower.services.wallet import WalletError
from .wallet_fixtures import WalletFixtures


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AppearanceTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()
        call_command("seed_tower_catalog", stdout=io.StringIO())

    def setUp(self):
        self.award(amount=300)
        self.player = TowerPlayer.objects.get(student=self.student)
        for hero in ("nis-coder", "nis-artist"):
            card = PlayerHero.objects.create(player=self.player, hero_id=hero, fragments=10, assembled_at=timezone.now())
            grant_character_set(player=self.player, hero=card.hero)

    def payload(self):
        selections = dict.fromkeys(SELECTION_SLOTS, "")
        selections.update(head="nis-coder-head", prop="nis-artist-prop", background="nis-artist-background",
                          ui_loader="nis-coder-ui-loader", ui_decoration="nis-artist-ui-decoration", companion="nis-coder-companion")
        return dict(shape="triangle", color="#10b981", theme="midnight", selections=selections)

    def test_mixed_selections_survive_new_session_without_currency_or_ownership_changes(self):
        before = WalletEntry.objects.count()
        save_appearance(student=self.student, **self.payload())
        self.player.refresh_from_db()
        self.assertEqual(self.player.avatar_accessories, {"head": "nis-coder-head", "prop": "nis-artist-prop"})
        self.assertEqual(self.player.appearance["companion"], "nis-coder-companion")
        self.assertEqual(WalletEntry.objects.count(), before)
        self.assertEqual(Wallet.objects.get(player=self.player).niscoin, 300)
        response = self.account_client().get(reverse("tower:home"), {"tab": "appearance"})
        self.assertEqual(response.context["wardrobe"]["values"], self.payload())
        self.assertContains(response, "appearance-form")
        self.assertEqual(response.context["wardrobe"]["values"]["selections"]["prop"], "nis-artist-prop")

    def test_unowned_wrong_slot_or_invalid_payload_is_atomic(self):
        save_appearance(student=self.student, **self.payload())
        self.player.refresh_from_db()
        baseline = copy.deepcopy(self.player.appearance)
        bad = [dict(self.payload(), color="url(javascript:alert(1))"), dict(self.payload(), theme="forest")]
        for slot, value in (("head", "crown"), ("prop", "nis-coder-head"), ("companion", "nis-biologist-companion"), ("head", [])):
            values = self.payload()
            values["selections"][slot] = value
            bad.append(values)
        bad.append(dict(self.payload(), selections={}))
        for values in bad:
            with self.subTest(values=values), self.assertRaises(WalletError):
                save_appearance(student=self.student, **values)
            self.player.refresh_from_db()
            self.assertEqual(self.player.appearance, baseline)

    def test_reset_and_legacy_purchases_remain_available(self):
        buy_cosmetic(student=self.student, cosmetic_id="cap", request_id=uuid.uuid4())
        buy_cosmetic(student=self.student, cosmetic_id="forest", request_id=uuid.uuid4())
        values = self.payload()
        values["selections"]["head"] = "cap"
        values["theme"] = "forest"
        save_appearance(student=self.student, **values)
        save_appearance(student=self.student, shape="square", color="#3b82f6", theme="midnight", selections=dict.fromkeys(SELECTION_SLOTS, ""))
        self.player.refresh_from_db()
        self.assertEqual((self.player.avatar_accessories, self.player.appearance, self.player.profile_theme), ({}, {}, "midnight"))
        self.assertEqual(self.player.cosmetics.count(), 14)

    def test_try_on_get_does_not_persist_and_unknown_set_is_ignored(self):
        client = self.account_client()
        for hero in ("nis-coder", "not-owned"):
            response = client.get(reverse("tower:home"), {"tab": "appearance", "try_set": hero})
            self.assertEqual(response.status_code, 200)
        self.player.refresh_from_db()
        self.assertEqual((self.player.appearance, self.player.avatar_accessories), ({}, {}))

    def test_html_and_json_save_and_csrf(self):
        client = self.account_client(csrf=True)
        self.assertEqual(client.post(reverse("tower:student_action_api", args=["appearance"]), self.payload(), content_type="application/json").status_code, 403)
        page = client.get(reverse("tower:home"), {"tab": "appearance"})
        token = client.cookies["csrftoken"].value
        response = client.post(reverse("tower:student_action_api", args=["appearance"]), self.payload(), content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        payload = self.payload()
        choices = payload.pop("selections")
        response = client.post(reverse("tower:student_action", args=["appearance"]), {**payload, **choices, "csrfmiddlewaretoken": token})
        self.assertRedirects(response, reverse("tower:home") + "?tab=avatar")

    def test_revocation_removes_global_decoration_and_prevents_saving(self):
        save_appearance(student=self.student, **self.payload())
        self.group.tower_enabled = False
        self.group.save(update_fields=["tower_enabled"])
        client = self.account_client()
        self.assertNotContains(client.get(reverse("student_portal_page")), 'id="tower-personal-strip"')
        self.assertEqual(client.post(reverse("tower:student_action_api", args=["appearance"]), self.payload(), content_type="application/json").status_code, 403)
        self.assertIsNone(saved_avatar(self.student))
        self.player.refresh_from_db()
        self.assertEqual(self.player.appearance["companion"], "nis-coder-companion")

    def test_personal_assets_are_not_exposed_to_another_student_or_teacher(self):
        save_appearance(student=self.student, **self.payload())
        other = self.account_client(self.other_student).get(reverse("student_portal_page"))
        self.assertNotContains(other, "nis-artist-background")
        self.assertNotContains(other, "tower-equipped-data")
        teacher = self.account_client(self.teacher).get(reverse("tower:teacher"))
        self.assertNotContains(teacher, "tower-equipped-data")

    def test_game_snapshot_has_shape_specific_attachments_and_survives_later_selection(self):
        save_appearance(student=self.student, **self.payload())
        lesson = Session.objects.create(title="Appearance race", author=self.teacher, status="running")
        lesson.allowed_classes.add(self.group)
        module = GameModule.objects.create(session=lesson, position=1, title="Race")
        round_obj = GameRound.objects.create(module=module, class_group=self.group, moderator=self.teacher, run_number=1)
        response = self.account_client().post(reverse("student_game_ready_api", args=[module.pk]))
        self.assertEqual(response.status_code, 200)
        participant = GameParticipant.objects.get(round=round_obj)
        saved = copy.deepcopy(participant.avatar_accessories)
        self.assertEqual(len(saved["_set_items"]), 2)
        self.assertEqual(set(saved["_set_items"][0]["matrices"]), {"square", "diamond", "triangle", "pentagon", "hexagon"})
        save_appearance(student=self.student, shape="square", color="#3b82f6", theme="midnight", selections=dict.fromkeys(SELECTION_SLOTS, ""))
        participant.refresh_from_db()
        self.assertEqual(_participant_row(participant)["avatar_accessories"], saved)
        with override_settings(TOWER_ENABLED=False):
            participant.refresh_from_db()
            del participant._tower_accessories
            self.assertEqual(_participant_row(participant)["avatar_accessories"], {})

    def test_platform_integration_and_translations_on_all_supported_pages(self):
        save_appearance(student=self.student, **self.payload())
        for language in ("ru", "kk", "en"):
            client = self.account_client()
            session = client.session
            session["ui_lang"] = language
            session.save()
            for page in ("student_portal_page", "student_dashboard_page", "student_exams_page"):
                response = client.get(reverse(page))
                self.assertContains(response, 'id="tower-personal-strip"')
                self.assertContains(response, 'id="tower-companion"')

    def test_unlocking_another_set_does_not_replace_chosen_appearance(self):
        values = self.payload()
        save_appearance(student=self.student, **values)
        card = PlayerHero.objects.get(player=self.player, hero_id="nis-artist")
        grant_character_set(player=self.player, hero=card.hero)
        self.player.refresh_from_db()
        self.assertEqual(appearance_context(self.player)["values"], values)
