import json
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.utils.html import escapejs

from .guest_views import claim_guest, release_guest
from .models import ClassGroup, Student, Teacher
from .security import auth_version


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class GuestAccountTests(TestCase):
    def setUp(self):
        self.teacher = Teacher.objects.create(full_name="Guest teacher", pin_hash="unused")
        self.group = ClassGroup.objects.create(name="Demo", owner=self.teacher, is_guest=True, guest_access_token=uuid.uuid4())
        self.url = f"/guest/{self.group.guest_access_token}/"
        self.students = [Student.objects.create(full_name=f"Guest {i}", class_group=self.group, pin_hash="!unused") for i in range(2)]
        self.teacher_client = Client()
        session = self.teacher_client.session
        session.update({"teacher_id":self.teacher.id, "teacher_auth_version":auth_version(self.teacher.pin_hash)})
        session.save()

    def enter(self, client):
        client.get(self.url)
        return client.post(self.url)

    def test_exclusive_allocation_full_and_repeated_entry(self):
        first, second, third = Client(), Client(), Client()
        self.assertEqual(self.enter(first).status_code, 302)
        first_id = first.session["student_id"]
        self.assertEqual(first.post(self.url).status_code, 302)
        self.assertEqual(first.session["student_id"], first_id)
        self.assertEqual(self.enter(second).status_code, 302)
        self.assertNotEqual(second.session["student_id"], first_id)
        self.assertEqual(self.enter(third).status_code, 409)
        self.assertNotIn("student_id", third.session)

    def test_logout_releases_and_old_token_cannot_release_new_owner(self):
        first = Client()
        self.enter(first)
        student_id, old_token = first.session["student_id"], first.session["guest_lease_token"]
        first.post("/api/auth/student-logout")
        second = Client()
        self.enter(second)
        self.assertEqual(second.session["student_id"], student_id)
        release_guest(student_id, old_token)
        self.assertEqual(second.get("/api/auth/student-me").status_code, 200)

    def test_expired_browser_cannot_access_reassigned_account(self):
        first = Client()
        self.enter(first)
        student_id = first.session["student_id"]
        Student.objects.filter(id=student_id).update(guest_lease_expires_at=timezone.now() - timedelta(seconds=1))
        second = Client()
        self.enter(second)
        self.assertEqual(second.session["student_id"], student_id)
        self.assertEqual(first.get("/api/auth/student-me").status_code, 401)
        self.assertEqual(second.get("/api/auth/student-me").status_code, 200)

    def test_request_renews_active_lease(self):
        self.enter(self.client)
        student_id = self.client.session["student_id"]
        Student.objects.filter(id=student_id).update(guest_lease_expires_at=timezone.now() + timedelta(minutes=1))
        self.client.get("/api/auth/student-me")
        self.assertGreater(Student.objects.get(id=student_id).guest_lease_expires_at, timezone.now() + timedelta(minutes=29))

    def test_no_pin_bypass_or_missing_lease_session(self):
        student = self.students[0]
        student.set_pin("123456")
        student.save()
        response = self.client.post("/api/auth/student-login", data=json.dumps({"full_name":student.full_name,"pin":"123456"}), content_type="application/json")
        self.assertEqual(response.status_code, 401)
        session = self.client.session
        session.update({"student_id":student.id, "student_class_id":self.group.id, "student_auth_version":auth_version(student.pin_hash)})
        session.save()
        self.assertEqual(self.client.get("/api/auth/student-me").status_code, 401)
        self.assertEqual(self.client.get("/api/student/exams/").status_code, 401)

    def test_only_one_guest_class_per_teacher_in_database_and_api(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ClassGroup.objects.create(name="Second", owner=self.teacher, is_guest=True)
        response = self.teacher_client.post("/api/teacher/classes/", data=json.dumps({"name":"Second","is_guest":True}), content_type="application/json")
        self.assertEqual(response.status_code, 409)

    def test_create_and_bulk_create_without_pin(self):
        for path, payload in [("/api/teacher/students/", {"full_name":"Guest extra"}), ("/api/teacher/students/bulk/", {"names":"Guest bulk 1\nGuest bulk 2"})]:
            response = self.teacher_client.post(path, data=json.dumps({**payload,"class_id":self.group.id}), content_type="application/json")
            self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.group.students.count(), 5)
        self.assertTrue(self.group.students.get(full_name="Guest extra").pin_hash.startswith("!"))

    def test_qr_is_owner_only_and_valid_svg(self):
        response = self.teacher_client.get(f"/teacher/classes/{self.group.id}/guest-qr/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<svg", response.content)
        self.assertEqual(self.client.get(f"/teacher/classes/{self.group.id}/guest-qr/").status_code, 302)

    def test_guest_memberships_and_transfers_are_rejected(self):
        ordinary = ClassGroup.objects.create(name="Private", owner=self.teacher)
        student = self.students[0]
        for path, payload in [(f"/api/teacher/students/{student.id}/", {"class_id":ordinary.id}), (f"/api/teacher/students/{student.id}/classes/", {"class_ids":[ordinary.id,self.group.id]})]:
            response = self.teacher_client.patch(path, data=json.dumps(payload), content_type="application/json")
            self.assertEqual(response.status_code, 400)

    def test_claim_is_idempotent_and_inactive_accounts_are_skipped(self):
        self.students[0].is_active = False
        self.students[0].save()
        token = uuid.uuid4()
        self.assertEqual(claim_guest(self.group, token).id, self.students[1].id)
        self.assertEqual(claim_guest(self.group, token).id, self.students[1].id)
        self.assertIsNone(claim_guest(self.group, uuid.uuid4()))

    def test_expired_nonce_can_claim_an_earlier_free_account(self):
        token = uuid.uuid4()
        Student.objects.filter(id=self.students[1].id).update(
            guest_lease_token=token, guest_lease_expires_at=timezone.now()-timedelta(seconds=1),
        )
        self.assertEqual(claim_guest(self.group, token).id, self.students[0].id)

    def test_regular_login_releases_guest_and_removes_guest_identity(self):
        self.enter(self.client)
        guest_id = self.client.session["student_id"]
        group = ClassGroup.objects.create(name="Regular", owner=self.teacher)
        student = Student(full_name="Regular student", class_group=group)
        student.set_pin("123456")
        student.save()
        response = self.client.post("/api/auth/student-login", data=json.dumps({"full_name":student.full_name,"pin":"123456"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("guest_lease_token", self.client.session)
        self.assertIsNone(Student.objects.get(id=guest_id).guest_lease_token)

    def test_get_does_not_allocate_and_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(self.url).status_code, 200)
        self.assertEqual(client.post(self.url).status_code, 403)
        self.assertFalse(Student.objects.filter(guest_lease_token__isnull=False).exists())

    def test_owner_can_release_all_guests_and_old_sessions_stay_invalid(self):
        first, second = Client(), Client()
        self.enter(first)
        self.enter(second)
        old_token = first.session["guest_lease_token"]
        response = self.teacher_client.post(f"/api/teacher/classes/{self.group.id}/release-guests/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["released_count"], 2)
        self.assertFalse(self.group.students.exclude(guest_lease_token=None).exists())
        self.assertFalse(self.group.students.exclude(guest_lease_expires_at=None).exists())
        for client in (first, second):
            self.assertEqual(client.get("/api/auth/guest-status").status_code, 401)
            self.assertNotIn("guest_visitor_token", client.session)
            self.assertEqual(client.get("/api/auth/student-me").status_code, 401)
        self.enter(first)
        self.assertNotEqual(first.session["guest_lease_token"], old_token)
        self.assertEqual(second.get("/api/auth/student-me").status_code, 401)
        self.group.refresh_from_db()
        self.assertEqual(f"/guest/{self.group.guest_access_token}/", self.url)
        self.assertEqual(self.group.students.filter(is_active=True).count(), 2)

    def test_release_is_owner_only_and_guest_class_only(self):
        self.enter(self.client)
        url = f"/api/teacher/classes/{self.group.id}/release-guests/"
        self.assertEqual(self.client.post(url).status_code, 401)
        self.assertEqual(Client().post(url).status_code, 401)
        self.assertEqual(self.teacher_client.get(url).status_code, 405)
        other = Teacher.objects.create(full_name="Other teacher", pin_hash="!")
        other_group = ClassGroup.objects.create(name="Other guests", owner=other, is_guest=True, guest_access_token=uuid.uuid4())
        other_student = Student.objects.create(full_name="Other guest", class_group=other_group, pin_hash="!")
        claim_guest(other_group, uuid.uuid4())
        ordinary = ClassGroup.objects.create(name="Regular", owner=self.teacher)
        for group in (other_group, ordinary):
            self.assertEqual(self.teacher_client.post(f"/api/teacher/classes/{group.id}/release-guests/").status_code, 404)
        self.teacher_client.post(url)
        other_student.refresh_from_db()
        self.assertIsNotNone(other_student.guest_lease_token)

    def test_release_requires_csrf(self):
        protected = Client(enforce_csrf_checks=True)
        protected.cookies = self.teacher_client.cookies
        self.assertEqual(protected.post(f"/api/teacher/classes/{self.group.id}/release-guests/").status_code, 403)

    def test_status_poll_does_not_extend_lease_and_guest_pages_include_watcher(self):
        self.enter(self.client)
        expires = timezone.now() + timedelta(minutes=1)
        Student.objects.filter(id=self.client.session["student_id"]).update(guest_lease_expires_at=expires)
        self.assertEqual(self.client.get("/api/auth/guest-status").status_code, 200)
        student = Student.objects.get(id=self.client.session["student_id"])
        self.assertEqual(student.guest_lease_expires_at, expires)
        response = self.client.get("/student/exams/")
        self.assertContains(response, 'setInterval(checkGuestSession, 5000)')
        self.assertContains(response, escapejs(self.url))
