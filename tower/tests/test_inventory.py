import io
import uuid

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from tower.models import (
    CharacterSetEntitlement, GameOperation, PlayerCharacterSet, PlayerCosmetic,
    PlayerHero, TowerAuditEvent, TowerPlayer, Wallet, WalletEntry,
)
from tower.services.appearance import SELECTION_SLOTS, save_appearance
from .wallet_fixtures import WalletFixtures


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class TeacherInventoryTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()
        call_command("seed_tower_catalog", stdout=io.StringIO())

    def setUp(self):
        self.client = self.account_client(self.teacher)
        self.url = f'{reverse("tower:teacher_inventory", args=[self.student.pk])}?class_id={self.group.pk}'

    def post(self, action, **values):
        payload = {"action": action, "request_id": str(uuid.uuid4()), "reason": "Teacher correction", **values}
        return self.client.post(self.url, payload)

    def test_roster_link_and_scope_while_inventory_is_empty(self):
        roster = self.client.get(reverse("tower:teacher"), {"class_id": self.group.pk})
        self.assertContains(roster, f'href="{self.url}">{self.student.full_name}</a>')
        self.assertContains(self.client.get(self.url), "Инвентарь ученика")
        self.assertFalse(TowerPlayer.objects.exists())
        other_teacher = self.account_client(self.other_teacher)
        self.assertEqual(other_teacher.get(self.url).status_code, 404)
        self.assertEqual(other_teacher.post(self.url, {"action": "grant_fragments"}).status_code, 404)
        self.assertEqual(self.account_client().get(self.url).status_code, 403)
        outsider = reverse("tower:teacher_inventory", args=[self.other_student.pk])
        self.assertEqual(self.client.get(f"{outsider}?class_id={self.group.pk}").status_code, 404)
        self.assertEqual(self.account_client(self.teacher, csrf=True).post(self.url, {}).status_code, 403)

    def test_both_balances_update_in_one_idempotent_operation_and_reject_stale_values(self):
        self.award(amount=40)
        request_id = uuid.uuid4()
        changes = {"previous_niscoin": 40, "previous_shards": 0, "niscoin": 10, "universal_shards": 7}
        self.assertRedirects(self.post("set_balances", request_id=request_id, **changes), self.url)
        self.assertRedirects(self.post("set_balances", request_id=request_id, **changes), self.url)
        wallet = Wallet.objects.get()
        self.assertEqual((wallet.niscoin, wallet.universal_shards), (10, 7))
        operation = GameOperation.objects.get(request_id=request_id)
        self.assertEqual(operation.actor_teacher, self.teacher)
        self.assertCountEqual(operation.entries.values_list("currency", "delta", "balance_after"), [
            ("niscoin", -30, 10), ("universal_shards", 7, 7),
        ])
        self.assertEqual(TowerAuditEvent.objects.filter(entry__operation=operation, class_group=self.group).count(), 2)

        self.award(amount=5)
        response = self.post("set_balances", **{**changes, "previous_niscoin": 10, "previous_shards": 7})
        self.assertRedirects(response, self.url)
        wallet.refresh_from_db()
        self.assertEqual((wallet.niscoin, wallet.universal_shards), (15, 7))
        self.assertEqual(GameOperation.objects.filter(action="inventory_edit").count(), 1)
        self.assertEqual(WalletEntry.objects.count(), 4)
        call_command("reconcile_tower_wallets", stdout=io.StringIO())

    def test_remove_character_revokes_set_clears_equipment_and_allows_reassembly(self):
        self.assertRedirects(self.post("grant_fragments", hero_id="nis-coder", quantity=4), self.url)
        self.assertEqual(self.post("grant_fragments", hero_id="nis-coder", quantity=7).status_code, 409)
        partial = PlayerHero.objects.get(hero_id="nis-coder")
        self.assertEqual(partial.fragments, 4)
        self.assertIsNone(partial.assembled_at)
        self.assertRedirects(self.post("grant_fragments", hero_id="nis-coder", quantity=6), self.url)
        self.assertRedirects(self.post("grant_fragments", hero_id="nis-artist", quantity=10), self.url)
        coder = PlayerHero.objects.get(hero_id="nis-coder")
        self.assertIsNotNone(coder.assembled_at)
        selections = dict.fromkeys(SELECTION_SLOTS, "")
        selections.update(head="nis-coder-head", prop="nis-artist-prop",
                          background="nis-coder-background", companion="nis-coder-companion")
        save_appearance(student=self.student, shape="square", color="#3b82f6", selections=selections, theme="midnight")

        removal_id = uuid.uuid4()
        remove = {"request_id": removal_id, "card_id": coder.pk, "fragments": coder.fragments}
        self.assertRedirects(self.post("remove_hero", **remove), self.url)
        self.assertRedirects(self.post("remove_hero", **remove), self.url)
        self.assertFalse(PlayerHero.objects.filter(pk=coder.pk).exists())
        self.assertEqual(PlayerCosmetic.objects.count(), 6)
        player = TowerPlayer.objects.get()
        self.assertEqual(player.avatar_accessories, {"prop": "nis-artist-prop"})
        self.assertEqual(player.appearance, {})
        event = TowerAuditEvent.objects.get(event_type="remove_hero")
        self.assertIn("nis-coder-head", event.metadata["removed_cosmetics"])
        self.assertEqual(event.actor_teacher, self.teacher)
        journal = self.account_client().get(reverse("tower:home"), {"hero": "nis-coder"})
        self.assertFalse(journal.context["hero_detail"]["set_received"])
        self.assertEqual([entry["hero_id"] for entry in journal.context["appearance_sets"]], ["nis-artist"])

        self.assertRedirects(self.post("grant_fragments", hero_id="nis-coder", quantity=10), self.url)
        self.assertEqual((PlayerCosmetic.objects.count(), CharacterSetEntitlement.objects.count(), PlayerCharacterSet.objects.count()),
                         (12, 2, 2))
        self.assertTrue(TowerAuditEvent.objects.filter(event_type="character_set_restored").exists())
        self.assertRedirects(self.post("remove_hero", **remove), self.url)
        self.assertTrue(PlayerHero.objects.filter(hero_id="nis-coder").exists())
        self.assertFalse(WalletEntry.objects.exists())
