import io
import json
import uuid
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import GameModule, GameParticipant, GameRound, Session
from core.game_views import _participant_row
from tower.models import (CosmeticDefinition, GameOperation, HeroDefinition, PackOffer, PackOpening,
                          PackVersion, PlayerCosmetic, PlayerHero, TowerPlayer, Wallet, WalletEntry)
from tower.services.collection import buy_cosmetic, exchange_fragments, open_pack, save_avatar, set_favorite
from tower.services.wallet import WalletError, adjust_wallet, student_wallet
from .wallet_fixtures import WalletFixtures


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CollectionTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()
        call_command("seed_tower_catalog", stdout=io.StringIO())

    def setUp(self):
        # These legacy fulfilment tests control ordinary slot draws independently.
        gate = patch("tower.services.collection._legendary_slot", return_value=None)
        gate.start()
        self.addCleanup(gate.stop)

    def pack(self, **values):
        return open_pack(student=self.student, offer="school", version="school-v2", request_id=values.get("request_id", uuid.uuid4()))

    def action(self, action, values, client=None):
        return (client or self.account_client()).post(reverse("tower:student_action_api", args=[action]), json.dumps(values), content_type="application/json")

    def shards(self, amount=100):
        adjust_wallet(admin=self.admin, student_id=self.student.pk, currency="universal_shards", delta=amount, reason="Test shards", request_id=uuid.uuid4())

    def test_catalog_seed_is_idempotent_without_player_rewards(self):
        call_command("seed_tower_catalog", stdout=io.StringIO())
        self.assertEqual((HeroDefinition.objects.count(), PackVersion.objects.count(), CosmeticDefinition.objects.filter(acquisition="purchase").count()), (12, 2, 7))
        self.assertFalse(TowerPlayer.objects.exists())

    @patch("tower.services.collection._draw", return_value=("nis-coder", "common"))
    def test_pack_debits_and_grants_once_with_persisted_result(self, draw):
        self.award(amount=100)
        key = uuid.uuid4()
        first = self.pack(request_id=key)
        self.assertEqual(Wallet.objects.get().niscoin, 50)
        self.assertEqual(PlayerHero.objects.get().fragments, 6)
        self.assertEqual(len(PackOpening.objects.get().drops), 3)
        self.assertEqual(TowerPlayer.objects.get().pack_misses, 1)
        self.assertEqual(self.pack(request_id=key), {**first, "replayed": True})
        self.assertEqual((PackOpening.objects.count(), draw.call_count), (1, 3))

    def test_insufficient_balance_rolls_back_lazy_wallet_and_all_side_effects(self):
        with self.assertRaises(WalletError):
            self.pack()
        self.assertFalse(Wallet.objects.exists())
        self.assertFalse(PlayerHero.objects.exists())
        self.assertFalse(GameOperation.objects.exists())

    @patch("tower.services.collection._draw", return_value=("nis-coder", "common"))
    def test_auto_assembly_overflow_and_ledger_reconciliation(self, draw):
        self.award(amount=50)
        player = TowerPlayer.objects.get()
        PlayerHero.objects.create(player=player, hero_id="nis-coder", fragments=8)
        result = self.pack()
        hero = PlayerHero.objects.get()
        self.assertEqual(hero.fragments, 10)
        self.assertIsNotNone(hero.assembled_at)
        self.assertEqual(Wallet.objects.get().universal_shards, 4)
        self.assertEqual([row["completed"] for row in result["drops"]], [True, False, False])
        call_command("reconcile_tower_wallets", stdout=io.StringIO())

    @patch("tower.services.collection.secrets.choice", side_effect=lambda values: values[0])
    @patch("tower.services.collection.secrets.randbelow", return_value=0)
    def test_fifth_common_only_pack_guarantees_a_rare_slot_and_resets_counter(self, randbelow, choice):
        self.award(amount=250)
        for _ in range(4):
            result = self.pack()
            self.assertTrue(all(row["rarity"] == "common" for row in result["drops"]))
        self.assertEqual(TowerPlayer.objects.get().pack_misses, 4)
        fifth = self.pack()
        self.assertTrue(fifth["guaranteed"])
        self.assertEqual(fifth["drops"][0]["rarity"], "rare")
        self.assertEqual(fifth["pity"], 0)

    @patch("tower.services.collection.PackOpening.objects.create", side_effect=RuntimeError("storage failure"))
    def test_fulfilment_failure_rolls_back_money_and_collection(self, create):
        self.award(amount=100)
        with self.assertRaises(RuntimeError):
            self.pack()
        self.assertEqual(Wallet.objects.get().niscoin, 100)
        self.assertFalse(PlayerHero.objects.exists())
        self.assertEqual(WalletEntry.objects.count(), 1)
        self.assertEqual(TowerPlayer.objects.get().pack_misses, 0)

    def test_stale_version_and_changed_retry_payload_do_not_charge(self):
        self.award(amount=100)
        payload = {"offer": "school", "version": "old", "request_id": str(uuid.uuid4())}
        self.assertEqual(self.action("pack", payload).status_code, 409)
        self.assertEqual(Wallet.objects.get().niscoin, 100)
        key = uuid.uuid4()
        self.pack(request_id=key)
        payload.update(version="other", request_id=str(key))
        self.assertEqual(self.action("pack", payload).json()["error"], "request_conflict")
        PackOffer.objects.filter(pk="school").update(is_active=False)
        self.assertTrue(self.pack(request_id=key)["replayed"])

    def test_published_versions_and_receipts_cannot_be_edited(self):
        with self.assertRaises(ValidationError):
            PackVersion.objects.get(slug="school-v2").save()
        self.award(amount=50)
        self.pack()
        with self.assertRaises(ValidationError):
            PackOpening.objects.get().delete()

    def test_targeted_exchange_checks_cost_assembles_and_is_idempotent(self):
        self.shards()
        values = dict(student=self.student, hero_id="nis-coder", quantity=10, request_id=uuid.uuid4())
        result = exchange_fragments(**values)
        self.assertTrue(result["drop"]["completed"])
        self.assertEqual(Wallet.objects.get().universal_shards, 80)
        self.assertTrue(exchange_fragments(**values)["replayed"])
        with self.assertRaises(WalletError):
            exchange_fragments(**{**values, "request_id": uuid.uuid4()})
        call_command("reconcile_tower_wallets", stdout=io.StringIO())

    def test_exchange_rejects_overfill_insufficient_shards_and_forged_quantities(self):
        self.shards(amount=1)
        values = {"hero_id": "nis-coder", "quantity": 1, "request_id": str(uuid.uuid4())}
        self.assertEqual(self.action("exchange", values).json()["error"], "insufficient_funds")
        for value in [True, 1.5, "1", 0, 11]:
            self.assertEqual(self.action("exchange", {**values, "quantity": value}).status_code, 400)
        self.assertFalse(PlayerHero.objects.exists())

    def test_favorites_are_only_assembled_cards_and_limited_to_three(self):
        self.shards(amount=1000)
        for hero_id in ["nis-coder", "nis-mathematician", "nis-chemist", "nis-artist"]:
            exchange_fragments(student=self.student, hero_id=hero_id, quantity=10, request_id=uuid.uuid4())
        for hero_id in ["nis-coder", "nis-mathematician", "nis-chemist"]:
            set_favorite(student=self.student, hero_id=hero_id, favorite=True)
        with self.assertRaises(WalletError):
            set_favorite(student=self.student, hero_id="nis-artist", favorite=True)
        set_favorite(student=self.student, hero_id="nis-coder", favorite=False)
        set_favorite(student=self.student, hero_id="nis-artist", favorite=True)
        with self.assertRaises(WalletError):
            set_favorite(student=self.student, hero_id="nis-musician", favorite=True)

    def test_cosmetic_purchase_once_and_loadout_requires_ownership_and_correct_slot(self):
        self.award(amount=100)
        values = dict(student=self.student, cosmetic_id="cap", request_id=uuid.uuid4())
        buy_cosmetic(**values)
        self.assertTrue(buy_cosmetic(**values)["replayed"])
        self.assertEqual(Wallet.objects.get().niscoin, 70)
        with self.assertRaises(WalletError):
            buy_cosmetic(**{**values, "request_id": uuid.uuid4()})
        avatar = dict(student=self.student, shape="hexagon", color="#10b981", accessories={"head": "cap", "glasses": "", "mustache": ""}, theme="midnight")
        player = save_avatar(**avatar)
        self.assertEqual(player.avatar_accessories, {"head": "cap"})
        for invalid in [dict(avatar, accessories={"head": "crown", "glasses": "", "mustache": ""}), dict(avatar, theme="forest"), dict(avatar, color="url(javascript:alert(1))")]:
            with self.assertRaises(WalletError):
                save_avatar(**invalid)

    def test_saved_appearance_is_snapshotted_in_games_and_revoked_access_hides_cosmetics(self):
        self.award(amount=100)
        buy_cosmetic(student=self.student, cosmetic_id="cap", request_id=uuid.uuid4())
        save_avatar(student=self.student, shape="triangle", color="#10b981", accessories={"head": "cap", "glasses": "", "mustache": ""}, theme="midnight")
        lesson = Session.objects.create(title="Game", author=self.teacher, status="running")
        lesson.allowed_classes.add(self.group)
        module = GameModule.objects.create(session=lesson, position=1, title="Race")
        round_obj = GameRound.objects.create(module=module, class_group=self.group, moderator=self.teacher, run_number=1)
        response = self.account_client().post(reverse("student_game_ready_api", args=[module.pk]))
        self.assertEqual(response.status_code, 200)
        participant = GameParticipant.objects.get(round=round_obj)
        self.assertEqual((participant.avatar_shape, participant.avatar_color, participant.avatar_accessories), ("triangle", "#10b981", {"head": "cap"}))
        self.assertEqual(_participant_row(participant)["avatar_accessories"], {"head": "cap"})
        with override_settings(TOWER_ENABLED=False):
            participant = GameParticipant.objects.get(pk=participant.pk)
            self.assertEqual(_participant_row(participant)["avatar_accessories"], {})

    def test_journal_states_private_results_and_html_post_redirect_get(self):
        self.award(amount=100)
        client = self.account_client()
        page = client.get(reverse("tower:home"))
        self.assertContains(page, 'data-collection-state="undiscovered"', count=12)
        self.assertNotContains(page, 'class="collection-card__portrait"')
        response = client.post(reverse("tower:student_action", args=["pack"]), {"offer": "school", "version": "school-v2", "request_id": uuid.uuid4()})
        self.assertEqual(response.status_code, 302)
        self.assertContains(client.get(response.url), 'id="pack-result"')
        self.assertEqual(self.account_client(self.other_student).get(response.url).status_code, 404)
        for tab in ["collection", "packs", "wallet", "avatar"]:
            self.assertEqual(client.get(reverse("tower:home"), {"tab": tab}).status_code, 200)

    def test_api_schema_csrf_and_revoked_permissions(self):
        self.award(amount=100)
        payload = {"offer": "school", "version": "school-v2", "request_id": str(uuid.uuid4())}
        self.assertEqual(self.action("pack", {**payload, "price": 0}).status_code, 400)
        self.assertEqual(self.action("pack", payload, self.account_client(self.teacher)).status_code, 403)
        csrf_client = self.account_client(csrf=True)
        self.assertEqual(self.action("pack", payload, csrf_client).status_code, 403)
        with override_settings(TOWER_ENABLED=False):
            self.assertEqual(self.action("pack", payload).status_code, 403)
        self.assertFalse(PackOpening.objects.exists())

    @patch("tower.services.collection._draw", return_value=("nis-coder", "common"))
    def test_opening_history_paginates_without_losing_old_results(self, draw):
        self.award(amount=600)
        for _ in range(12):
            self.pack()
        page = self.account_client().get(reverse("tower:home"), {"tab": "packs"})
        self.assertEqual(len(page.context["recent_openings"]), 10)
        older = self.account_client().get(reverse("tower:home") + page.context["older_openings_url"])
        self.assertEqual(len(older.context["recent_openings"]), 2)

    @patch("tower.services.collection.secrets.choice", side_effect=lambda values: values[0])
    def test_draw_boundaries_match_published_weights_even_when_json_key_order_changes(self, choice):
        from tower.services.collection import _draw
        config = PackVersion.objects.get(slug="school-v1").config
        config["weights"] = {key: config["weights"][key] for key in ("rare", "legendary", "common", "epic")}
        for ticket, rarity in [(0,"common"), (69,"common"), (70,"rare"), (91,"rare"), (92,"epic"), (98,"epic"), (99,"legendary")]:
            with patch("tower.services.collection.secrets.randbelow", return_value=ticket):
                self.assertEqual(_draw(config)[1], rarity)
        for ticket, rarity in [(0,"rare"), (21,"rare"), (22,"epic"), (28,"epic"), (29,"legendary")]:
            with patch("tower.services.collection.secrets.randbelow", return_value=ticket):
                self.assertEqual(_draw(config, guaranteed=True)[1], rarity)
