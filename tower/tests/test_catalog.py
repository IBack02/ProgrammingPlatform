import json

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings

from tower.catalog import MANIFEST_PATH, portrait_url, presentation_catalog


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=TEST_STORAGES, TOWER_ASSET_BASE_URL="https://images.example.org")
class CardPresentationTests(SimpleTestCase):
    def test_every_character_has_exact_rarity_and_star_mapping(self):
        catalog = presentation_catalog()
        self.assertEqual(len(catalog["cards"]), 12)
        for rarity, count in [("common", 2), ("rare", 3), ("epic", 4), ("legendary", 5)]:
            cards = [card for card in catalog["cards"] if card["rarity"] == rarity]
            self.assertEqual(len(cards), 3)
            for card in cards:
                html = render_to_string("tower/_collection_card.html", {"card": card})
                self.assertEqual(html.count('<svg '), count)
                self.assertIn('class="collection-card__portrait"', html)
                self.assertIn('class="collection-card__frame"', html)
                self.assertIn(f'https://images.example.org/chars/{card["id"]}.png', html)
                self.assertIsNotNone(finders.find(f"tower/frames/{rarity}.webp"))

    def test_all_portrait_names_are_unique_and_manifest_has_portrait_aspect(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["aspect_ratio"], [2, 3])
        portraits = [card["portrait"] for card in manifest["cards"]]
        self.assertEqual(len(set(portraits)), 12)
        self.assertTrue(all(path.startswith("chars/nis-") and path.endswith(".png") for path in portraits))

    def test_asset_base_can_change_without_rewriting_card_paths(self):
        with override_settings(TOWER_ASSET_BASE_URL="https://cdn.example.org/assets/"):
            self.assertEqual(portrait_url("chars/nis-coder.png"), "https://cdn.example.org/assets/chars/nis-coder.png")

    def test_unsafe_base_or_path_is_rejected(self):
        for base in ["javascript:alert(1)", "http://cdn.example.org", "https://u:p@cdn.example.org", "https://cdn.example.org/?token=secret"]:
            with self.subTest(base=base), override_settings(TOWER_ASSET_BASE_URL=base), self.assertRaises(ImproperlyConfigured):
                portrait_url("chars/nis-coder.png")
        for path in ["../secrets.png", "/chars/nis-coder.png", "chars/../secrets.png", "chars\\x.png"]:
            with self.subTest(path=path), self.assertRaises(ImproperlyConfigured):
                portrait_url(path)

    def test_rarity_labels_support_three_languages(self):
        for lang, expected in [("ru", "Легендарная"), ("kk", "Аңызға айналған"), ("en", "Legendary")]:
            self.assertEqual(presentation_catalog(lang)["rarities"][-1]["label"], expected)


@override_settings(TOWER_ENABLED=True, STORAGES=TEST_STORAGES, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CardPreviewAccessTests(TestCase):
    def test_only_authorized_preview_contains_the_catalogue(self):
        admin = get_user_model().objects.create_superuser(username="catalog-admin", password="password")
        self.client.force_login(admin)
        response = self.client.get("/teacher/tower/preview/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-card-rarity=', count=12)
        self.assertEqual(response.content.decode().count('<svg '), 42)
        self.client.logout()
        self.assertEqual(self.client.get("/teacher/tower/preview/").status_code, 302)

    def test_student_landing_does_not_claim_all_cards_are_owned(self):
        import io
        from django.core.management import call_command
        from core.models import ClassGroup, Student, Teacher
        from core.security import auth_version

        call_command("seed_tower_catalog", stdout=io.StringIO())

        teacher = Teacher.objects.create(full_name="Catalogue teacher", pin_hash="unused", can_manage_tower=True)
        group = ClassGroup.objects.create(name="Catalogue class", owner=teacher, tower_enabled=True)
        student = Student.objects.create(full_name="Catalogue student", class_group=group, pin_hash="unused")
        session = self.client.session
        session.update({"student_id": student.pk, "student_class_id": group.pk, "student_auth_version": auth_version(student.pin_hash)})
        session.save()
        self.client.post("/tower/login/", {"password": "Collection7!", "confirmation": "Collection7!"})
        response = self.client.get("/tower/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-collection-state="undiscovered"', count=12)
        self.assertNotContains(response, 'class="collection-card__portrait"')
