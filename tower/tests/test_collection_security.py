import io
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth.hashers import check_password
from django.db import connection, connections, close_old_connections
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.security import auth_version
from tower.collection_security import SESSION_KEY, set_initial_password
from tower.models import CollectionCredential, GameOperation, TowerAuditEvent, TowerPlayer, Wallet
from .wallet_fixtures import WalletFixtures


@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CollectionSecurityTests(WalletFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.create_accounts()
        call_command("seed_tower_catalog", stdout=io.StringIO())

    def setUp(self):
        self.client = self.account_client(collection_access=False)
        self.login_url = reverse("tower:collection_login")

    def enter(self, password="Collection7!", **extra):
        return self.client.post(self.login_url, {"password": password, "confirmation": password, **extra})

    def test_first_visit_prompts_setup_without_creating_wallet_or_password(self):
        response = self.client.get(reverse("tower:home"), {"tab": "packs"}, follow=True)
        self.assertTemplateUsed(response, "tower/collection_login.html")
        self.assertTrue(response.context["password_setup"])
        self.assertFalse(CollectionCredential.objects.exists())
        self.assertFalse(TowerPlayer.objects.exists())
        self.assertIn("no-store", response["Cache-Control"])

    def test_setup_hashes_separate_password_rotates_session_and_opens_journal(self):
        before = self.client.session.session_key
        response = self.enter(next="/tower/?tab=packs")
        self.assertRedirects(response, "/tower/?tab=packs")
        credential = CollectionCredential.objects.get(student=self.student)
        self.assertNotEqual(credential.password_hash, "Collection7!")
        self.assertTrue(check_password("Collection7!", credential.password_hash))
        self.student.refresh_from_db()
        self.assertEqual(self.student.pin_hash, "unused")
        self.assertNotEqual(self.client.session.session_key, before)

    def test_password_bounds_letters_confirmation_and_no_password_echo(self):
        for password in ("Abc12", "a" * 16, "123456", "a bc12", "abc\n12"):
            with self.subTest(password=password):
                response = self.enter(password)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(CollectionCredential.objects.exists())
                self.assertNotIn(f'value="{password}"', response.content.decode())
        self.assertEqual(self.enter("Абв123", confirmation="Другой").status_code, 400)
        self.assertFalse(CollectionCredential.objects.exists())
        self.assertEqual(self.enter("Абв123").status_code, 302)
        self.client.post(reverse("tower:collection_lock"))
        self.assertEqual(self.enter("Абв123").status_code, 302)

    def test_exact_length_boundaries_are_accepted(self):
        for length in (6, 15):
            CollectionCredential.objects.all().delete()
            self.client.post(reverse("tower:collection_lock"))
            self.assertEqual(self.enter("a" * length).status_code, 302)

    def test_next_entry_requires_password_and_cannot_replace_existing_one(self):
        self.enter()
        self.client.post(reverse("tower:collection_lock"))
        self.assertFalse(self.client.get(self.login_url).context["password_setup"])
        self.assertEqual(self.enter("different7").status_code, 400)
        self.assertEqual(self.client.get(reverse("tower:home")).status_code, 302)
        self.assertIsNone(set_initial_password(self.student, "replacement"))
        self.assertTrue(check_password("Collection7!", CollectionCredential.objects.get().password_hash))
        self.assertEqual(self.enter().status_code, 302)

    def test_locked_wallet_and_every_action_are_denied_without_mutation(self):
        self.award()
        wallet = Wallet.objects.get()
        for action in ("pack", "exchange", "cosmetic", "avatar", "appearance", "favorite"):
            response = self.client.post(reverse("tower:student_action_api", args=[action]), "{}", content_type="application/json")
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"], "collection_locked")
        response = self.client.get(reverse("tower:wallet"))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("wallet", response.json())
        self.assertEqual(self.client.post(reverse("tower:student_action", args=["pack"])).status_code, 302)
        wallet.refresh_from_db()
        self.assertEqual(wallet.niscoin, 100)
        self.assertEqual(GameOperation.objects.count(), 1)

    def test_csrf_required_for_setup_login_and_lock(self):
        client = self.account_client(collection_access=False, csrf=True)
        self.assertEqual(client.post(self.login_url, {"password": "Collection7!", "confirmation": "Collection7!"}).status_code, 403)
        page = client.get(self.login_url)
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post(self.login_url, {"password": "Collection7!", "confirmation": "Collection7!"}, HTTP_X_CSRFTOKEN=token).status_code, 302)
        self.assertEqual(client.post(reverse("tower:collection_lock")).status_code, 403)

    def test_retry_throttle_survives_new_session_and_new_ip(self):
        self.enter()
        self.client.post(reverse("tower:collection_lock"))
        for _ in range(4):
            self.assertEqual(self.enter("wrongpass").status_code, 400)
        response = self.enter("wrongpass")
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)
        self.client = self.account_client(collection_access=False)
        self.assertEqual(self.client.post(self.login_url, {"password": "Collection7!"}, REMOTE_ADDR="192.0.2.1").status_code, 429)

    def test_lesson_navigation_keeps_journal_unlocked_until_account_logout(self):
        self.enter()
        self.assertEqual(self.client.get(reverse("tower:status")).status_code, 200)
        self.assertEqual(self.client.get(reverse("tower:wallet")).status_code, 200)
        self.client.get("/student/")
        self.client.get("/student/dashboard/")
        self.client.get("/student/exams/")
        self.assertIn(SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(reverse("tower:wallet")).status_code, 200)
        self.assertEqual(self.client.get(reverse("tower:home")).status_code, 200)
        self.client.post(reverse("student_logout_page"))
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(reverse("tower:wallet")).status_code, 401)
        self.client = self.account_client(collection_access=False)
        self.assertEqual(self.client.get(reverse("tower:wallet")).status_code, 403)
        self.assertEqual(self.enter().status_code, 302)

    def test_no_separate_idle_timeout_but_student_switch_invalidates_grant(self):
        self.enter()
        session = self.client.session
        # Old grants from the previous release also lose their separate idle timeout.
        session[SESSION_KEY] = {**session[SESSION_KEY], "last_seen": timezone.now().timestamp() - 6 * 3600}
        session.save()
        self.assertEqual(self.client.get(reverse("tower:wallet")).status_code, 200)
        session = self.client.session
        session.update({"student_id": self.other_student.pk, "student_class_id": self.other_student.class_group_id,
                        "student_auth_version": auth_version(self.other_student.pin_hash)})
        session.save()
        self.assertEqual(self.client.get(reverse("tower:wallet")).status_code, 403)

    def test_api_logout_also_clears_collection_access(self):
        self.enter()
        self.assertEqual(self.client.post(reverse("student_logout")).status_code, 200)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_disabled_access_and_guests_cannot_set_password(self):
        self.group.tower_enabled = False
        self.group.save()
        self.assertEqual(self.enter().status_code, 403)
        self.assertFalse(CollectionCredential.objects.exists())

    def test_next_url_is_restricted_to_journal(self):
        for next_url in ("https://evil.example/tower/", "//evil.example/tower/", "/teacher/", "/tower/actions/pack/", "/tower/\\evil", "//[invalid"):
            self.client.post(reverse("tower:collection_lock"))
            self.assertEqual(self.enter(next=next_url).url, "/tower/")

    def test_teacher_reset_invalidates_other_browser_and_preserves_wallet(self):
        self.award()
        self.enter()
        other_browser = self.account_client()
        url = reverse("tower:collection_password_reset", args=[self.student.pk])
        teacher = self.account_client(self.teacher)
        self.assertEqual(teacher.get(url, {"class_id": self.group.pk}).status_code, 200)
        self.assertTrue(CollectionCredential.objects.exists())
        self.assertEqual(self.account_client(self.other_teacher).post(url, {"class_id": self.group.pk}).status_code, 404)
        self.assertEqual(self.client.post(url, {"class_id": self.group.pk}).status_code, 403)
        self.assertEqual(teacher.post(url, {"class_id": self.group.pk}).status_code, 302)
        self.assertFalse(CollectionCredential.objects.exists())
        self.assertEqual(other_browser.get(reverse("tower:wallet")).status_code, 403)
        self.assertTrue(self.client.get(self.login_url).context["password_setup"])
        self.assertEqual(Wallet.objects.get().niscoin, 100)
        self.assertEqual(TowerAuditEvent.objects.get(event_type="collection_password_reset").metadata, {})

    def test_main_login_clears_previous_collection_grant(self):
        from core.views import _clear_student_auth
        from django.test import RequestFactory
        request = RequestFactory().get("/")
        request.session = {SESSION_KEY: {"student_id": self.student.pk}}
        _clear_student_auth(request)
        self.assertNotIn(SESSION_KEY, request.session)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
@override_settings(TOWER_ENABLED=True, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CollectionSetupConcurrencyTests(WalletFixtures, TransactionTestCase):
    def test_only_one_concurrent_first_password_is_saved(self):
        self.create_accounts()
        barrier = Barrier(2)

        def setup(password):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                credential = set_initial_password(self.student, password)
                return password if credential else None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(setup, password) for password in ("Firstpass7", "Otherpass8")]
            results = [future.result(timeout=20) for future in futures]
        winners = [result for result in results if result]
        self.assertEqual(len(winners), 1)
        self.assertEqual(CollectionCredential.objects.count(), 1)
        self.assertTrue(check_password(winners[0], CollectionCredential.objects.get().password_hash))
