import json
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import ClassGroup, Student, StudentClassMembership, Teacher
from core.security import auth_version


@override_settings(
    TOWER_ENABLED=True,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class TowerAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = Teacher.objects.create(full_name="Tower teacher", pin_hash="unused", can_manage_tower=True)
        cls.ordinary_teacher = Teacher.objects.create(full_name="Ordinary teacher", pin_hash="unused")
        cls.other_teacher = Teacher.objects.create(full_name="Other tower teacher", pin_hash="unused", can_manage_tower=True)
        cls.group = ClassGroup.objects.create(name="Enabled", owner=cls.teacher, tower_enabled=True)
        cls.disabled_group = ClassGroup.objects.create(name="Disabled", owner=cls.teacher)
        cls.ordinary_group = ClassGroup.objects.create(name="Ordinary", owner=cls.ordinary_teacher)
        cls.other_group = ClassGroup.objects.create(name="Other", owner=cls.other_teacher, tower_enabled=True)
        cls.student = Student.objects.create(full_name="Tower student", class_group=cls.group, pin_hash="unused")
        cls.ordinary_student = Student.objects.create(full_name="Ordinary student", class_group=cls.ordinary_group, pin_hash="unused")
        cls.admin = get_user_model().objects.create_superuser(username="tower-admin", password="password")
        cls.staff = get_user_model().objects.create_user(username="tower-staff", password="password", is_staff=True)
        cls.staff.user_permissions.set(Permission.objects.filter(
            content_type__app_label="core", content_type__model__in=["teacher", "classgroup"],
        ))

    def student_client(self, student=None, **session_values):
        student = student or self.student
        client = Client()
        session = client.session
        session.update({
            "student_id": student.pk,
            "student_class_id": student.class_group_id,
            "student_auth_version": auth_version(student.pin_hash),
            **session_values,
        })
        session.save()
        return client

    def teacher_client(self, teacher=None):
        teacher = teacher or self.teacher
        client = Client()
        session = client.session
        session.update({"teacher_id": teacher.pk, "teacher_auth_version": auth_version(teacher.pin_hash)})
        session.save()
        return client

    def set_access(self, client, group=None, value=True):
        return client.patch(
            reverse("tower:class_access", args=[(group or self.disabled_group).pk]),
            data=json.dumps({"tower_enabled": value}), content_type="application/json",
        )

    def test_new_records_default_to_no_tower_access(self):
        teacher = Teacher.objects.create(full_name="New teacher", pin_hash="unused")
        group = ClassGroup.objects.create(name="New class", owner=teacher)
        self.assertFalse(teacher.can_manage_tower)
        self.assertFalse(group.tower_enabled)

    def test_anonymous_html_redirects_to_correct_login_and_api_returns_401(self):
        for name, login in [("tower:home", "/student/login/"), ("tower:teacher", "/teacher/login/")]:
            with self.subTest(name=name):
                self.assertRedirects(self.client.get(reverse(name)), login, fetch_redirect_response=False)
        for name in ["tower:status", "tower:teacher_classes"]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"], "not_authenticated")

    def test_enabled_student_can_enter_and_only_sees_eligible_memberships(self):
        StudentClassMembership.objects.create(student=self.student, class_group=self.disabled_group)
        client = self.student_client()
        self.assertEqual(client.get(reverse("tower:home")).status_code, 200)
        response = client.get(reverse("tower:status"))
        self.assertEqual(response.json(), {
            "ok": True, "student_id": self.student.pk,
            "classes": [{"id": self.group.pk, "name": self.group.name}],
        })
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_disabled_class_and_ordinary_teacher_class_cannot_enter(self):
        student = Student.objects.create(full_name="Disabled student", class_group=self.disabled_group, pin_hash="unused")
        ClassGroup.objects.filter(pk=self.ordinary_group.pk).update(tower_enabled=True)
        for account in [student, self.ordinary_student]:
            with self.subTest(student=account.pk):
                client = self.student_client(account)
                self.assertEqual(client.get(reverse("tower:home")).status_code, 403)
                self.assertEqual(client.get(reverse("tower:status")).status_code, 403)

    def test_additional_membership_grants_access_and_selected_class_is_not_authority(self):
        client = self.student_client(self.ordinary_student, student_selected_class_id=self.group.pk)
        self.assertEqual(client.get(reverse("tower:status")).status_code, 403)
        StudentClassMembership.objects.create(student=self.ordinary_student, class_group=self.group)
        self.assertEqual(client.get(reverse("tower:status")).status_code, 200)
        StudentClassMembership.objects.filter(student=self.ordinary_student, class_group=self.group).delete()
        self.assertEqual(client.get(reverse("tower:status")).status_code, 403)

    def test_primary_class_is_supported_without_membership_row_and_not_duplicated(self):
        client = self.student_client()
        self.assertEqual(len(client.get(reverse("tower:status")).json()["classes"]), 1)
        StudentClassMembership.objects.filter(student=self.student, class_group=self.group).delete()
        self.assertEqual(client.get(reverse("tower:status")).status_code, 200)

    def test_revoked_teacher_permission_immediately_removes_access_and_menu(self):
        student_client, teacher_client = self.student_client(), self.teacher_client()
        self.assertContains(student_client.get("/student/dashboard/"), 'href="/tower/"')
        self.assertContains(teacher_client.get("/teacher/classes/"), 'href="/teacher/tower/"')
        Teacher.objects.filter(pk=self.teacher.pk).update(can_manage_tower=False)
        self.assertEqual(student_client.get(reverse("tower:status")).status_code, 403)
        self.assertEqual(teacher_client.get(reverse("tower:teacher_classes")).status_code, 403)
        self.assertNotContains(student_client.get("/student/dashboard/"), 'href="/tower/"')
        self.assertNotContains(teacher_client.get("/teacher/classes/"), 'href="/teacher/tower/"')

    def test_second_enabled_class_preserves_access_after_first_owner_revocation(self):
        StudentClassMembership.objects.create(student=self.student, class_group=self.other_group)
        Teacher.objects.filter(pk=self.teacher.pk).update(can_manage_tower=False)
        response = self.student_client().get(reverse("tower:status"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["classes"], [{"id": self.other_group.pk, "name": self.other_group.name}])

    def test_ownerless_or_inactive_owner_does_not_grant_access(self):
        client = self.student_client()
        Teacher.objects.filter(pk=self.teacher.pk).update(is_active=False)
        self.assertEqual(client.get(reverse("tower:status")).status_code, 403)
        ClassGroup.objects.filter(pk=self.group.pk).update(owner=None)
        self.assertEqual(client.get(reverse("tower:status")).status_code, 403)

    def test_inactive_student_and_invalid_auth_versions_fail(self):
        client = self.student_client(student_auth_version="forged")
        self.assertEqual(client.get(reverse("tower:status")).status_code, 401)
        client = self.student_client()
        Student.objects.filter(pk=self.student.pk).update(is_active=False)
        self.assertEqual(client.get(reverse("tower:status")).status_code, 401)
        client = self.teacher_client()
        Teacher.objects.filter(pk=self.teacher.pk).update(pin_hash="changed")
        self.assertEqual(client.get(reverse("tower:teacher_classes")).status_code, 401)

    def test_real_guest_with_allowed_additional_membership_cannot_enter(self):
        group = ClassGroup.objects.create(name="Guest", owner=self.teacher, is_guest=True, guest_access_token=uuid.uuid4())
        token = uuid.uuid4()
        guest = Student.objects.create(
            full_name="Guest student", class_group=group, pin_hash="unused", guest_lease_token=token,
            guest_lease_expires_at=timezone.now() + timedelta(minutes=10),
        )
        StudentClassMembership.objects.create(student=guest, class_group=self.group)
        response = self.student_client(guest, guest_lease_token=str(token)).get(reverse("tower:status"))
        self.assertEqual(response.status_code, 403)

    def test_lease_marked_regular_account_is_excluded(self):
        client = self.student_client(guest_lease_token=str(uuid.uuid4()))
        self.assertEqual(client.get(reverse("tower:status")).status_code, 403)
        Student.objects.filter(pk=self.student.pk).update(guest_lease_token=uuid.uuid4())
        self.assertEqual(self.student_client().get(reverse("tower:status")).status_code, 403)

    @override_settings(TOWER_ENABLED=False)
    def test_global_switch_hides_links_and_denies_every_tower_route(self):
        student_client, teacher_client = self.student_client(), self.teacher_client()
        for client, names in [(student_client, ["tower:home", "tower:status"]),
                              (teacher_client, ["tower:teacher", "tower:preview", "tower:teacher_classes"])]:
            for name in names:
                with self.subTest(name=name):
                    self.assertEqual(client.get(reverse(name)).status_code, 403)
        self.assertEqual(self.set_access(teacher_client).status_code, 403)
        self.assertNotContains(student_client.get("/student/"), 'href="/tower/"')
        self.assertNotContains(teacher_client.get("/teacher/classes/"), 'href="/teacher/tower/"')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("tower:teacher")).status_code, 403)

    def test_ordinary_accounts_have_no_tower_navigation_and_cannot_manage(self):
        student_client = self.student_client(self.ordinary_student)
        teacher_client = self.teacher_client(self.ordinary_teacher)
        self.assertNotContains(student_client.get("/student/"), 'href="/tower/"')
        self.assertNotContains(student_client.get("/student/dashboard/"), 'href="/tower/"')
        self.assertNotContains(teacher_client.get("/teacher/classes/"), 'href="/teacher/tower/"')
        for client in [student_client, teacher_client, self.student_client()]:
            self.assertEqual(client.get(reverse("tower:teacher_classes")).status_code, 403)
            self.assertEqual(self.set_access(client).status_code, 403)

    def test_disabled_service_explains_global_switch_to_assigned_teacher(self):
        client = self.teacher_client()
        client.cookies["ui_lang"] = "ru"
        with override_settings(TOWER_ENABLED=False):
            page = client.get(reverse("tower:preview"))
            self.assertContains(page, "отключён на всём сайте", status_code=403)
            self.assertNotContains(page, "открывает учитель для выбранных классов", status_code=403)
            self.assertEqual(client.get(reverse("tower:teacher_classes")).json()["error"], "tower_disabled")
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.can_manage_tower)
        self.assertEqual(client.get(reverse("tower:preview")).status_code, 200)

    def test_unassigned_teacher_sees_permission_message_instead_of_student_hint(self):
        client = self.teacher_client(self.ordinary_teacher)
        client.cookies["ui_lang"] = "ru"
        page = client.get(reverse("tower:preview"))
        self.assertContains(page, "Для этого учительского аккаунта", status_code=403)
        self.assertNotContains(page, "открывает учитель для выбранных классов", status_code=403)

    def test_staff_without_teacher_session_gets_teacher_login_link(self):
        self.client.force_login(self.staff)
        self.client.cookies["ui_lang"] = "ru"
        page = self.client.get(reverse("tower:preview"))
        self.assertContains(page, 'href="/teacher/login/"', status_code=403)
        self.assertContains(page, "Войти как учитель", status_code=403)
        self.assertEqual(self.client.get(reverse("tower:teacher_classes")).status_code, 403)

    @override_settings(TOWER_ENABLED=False)
    def test_superuser_sees_disabled_service_notice_in_teacher_admin(self):
        self.client.force_login(self.admin)
        for url in [reverse("admin:core_teacher_changelist"), reverse("admin:core_teacher_change", args=[self.teacher.pk])]:
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), "TOWER_ENABLED=true")
        self.assertContains(self.client.get(reverse("tower:preview")), "TOWER_ENABLED=true", status_code=403)
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(reverse("admin:core_teacher_changelist")), "TOWER_ENABLED=true")

    def test_enabled_service_has_no_disabled_notice_in_admin(self):
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse("admin:core_teacher_changelist")), "TOWER_ENABLED=true")

    def test_teacher_lists_only_own_classes_and_can_toggle_access(self):
        client = self.teacher_client()
        response = client.get(reverse("tower:teacher_classes"))
        self.assertEqual({row["id"] for row in response.json()["classes"]}, {self.group.pk, self.disabled_group.pk})
        self.assertEqual(self.set_access(client).status_code, 200)
        self.disabled_group.refresh_from_db()
        self.assertTrue(self.disabled_group.tower_enabled)
        self.assertEqual(self.set_access(client, value=False).status_code, 200)
        self.disabled_group.refresh_from_db()
        self.assertFalse(self.disabled_group.tower_enabled)

    def test_teacher_cannot_change_or_infer_foreign_class_access(self):
        self.assertEqual(self.set_access(self.teacher_client(), self.other_group).status_code, 404)
        self.other_group.refresh_from_db()
        self.assertTrue(self.other_group.tower_enabled)

    def test_html_checkbox_saves_and_revokes_student_access(self):
        client = self.teacher_client()
        self.assertContains(client.get(reverse("tower:teacher")), 'name="tower_enabled"')
        response = client.post(reverse("tower:teacher"), {"class_id": self.group.pk})
        self.assertRedirects(response, reverse("tower:teacher"), fetch_redirect_response=False)
        self.assertEqual(self.student_client().get(reverse("tower:status")).status_code, 403)
        client.post(reverse("tower:teacher"), {"class_id": self.group.pk, "tower_enabled": "on"})
        self.assertEqual(self.student_client().get(reverse("tower:status")).status_code, 200)

    def test_html_checkbox_cannot_modify_foreign_class(self):
        response = self.teacher_client().post(reverse("tower:teacher"), {"class_id": self.other_group.pk})
        self.assertEqual(response.status_code, 404)
        self.other_group.refresh_from_db()
        self.assertTrue(self.other_group.tower_enabled)

    def test_checkbox_and_api_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session.update({"teacher_id": self.teacher.pk, "teacher_auth_version": auth_version(self.teacher.pin_hash)})
        session.save()
        self.assertEqual(self.set_access(client).status_code, 403)
        self.assertEqual(client.post(reverse("tower:teacher"), {"class_id": self.group.pk}).status_code, 403)
        client.get(reverse("tower:teacher"))
        response = client.patch(
            reverse("tower:class_access", args=[self.disabled_group.pk]),
            data=json.dumps({"tower_enabled": True}), content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 200)

    def test_class_api_rejects_non_boolean_and_extra_fields(self):
        client = self.teacher_client()
        for body in ['null', '[]', '{}', '{', '{"tower_enabled": 1}', '{"tower_enabled": "false"}',
                     '{"tower_enabled": true, "owner": 1}']:
            with self.subTest(body=body):
                response = client.patch(reverse("tower:class_access", args=[self.disabled_group.pk]), data=body, content_type="application/json")
                self.assertEqual(response.status_code, 400)
        self.disabled_group.refresh_from_db()
        self.assertFalse(self.disabled_group.tower_enabled)

    def test_guest_class_is_rejected_by_management_and_database_constraint(self):
        group = ClassGroup.objects.create(name="Guest", owner=self.teacher, is_guest=True)
        self.assertEqual(self.set_access(self.teacher_client(), group).status_code, 400)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ClassGroup.objects.filter(pk=group.pk).update(tower_enabled=True)

    def test_legacy_class_endpoints_cannot_set_tower_fields(self):
        for teacher in [self.teacher, self.ordinary_teacher]:
            client = self.teacher_client(teacher)
            group = self.group if teacher == self.teacher else self.ordinary_group
            for field in ["tower_enabled", "can_manage_tower"]:
                with self.subTest(teacher=teacher.pk, field=field):
                    response = client.post("/api/teacher/classes/", data=json.dumps({"name": "Injected", field: True}), content_type="application/json")
                    self.assertEqual(response.status_code, 403)
                    response = client.patch(f"/api/teacher/classes/{group.pk}/", data=json.dumps({"name": "Renamed", field: True}), content_type="application/json")
                    self.assertEqual(response.status_code, 403)
            group.refresh_from_db()
            self.assertNotEqual(group.name, "Renamed")

    def test_normal_class_rename_preserves_access(self):
        response = self.teacher_client().patch(f"/api/teacher/classes/{self.group.pk}/", data=json.dumps({"name": "Renamed"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertTrue(self.group.tower_enabled)

    def test_preview_requires_manager_and_does_not_impersonate_student(self):
        self.assertEqual(self.teacher_client().get(reverse("tower:preview")).status_code, 200)
        self.assertEqual(self.teacher_client(self.ordinary_teacher).get(reverse("tower:preview")).status_code, 403)
        self.assertEqual(self.teacher_client().get(reverse("tower:status")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("tower:preview")).status_code, 200)
        self.assertEqual(self.client.get(reverse("tower:status")).status_code, 403)

    def test_admin_can_manage_but_unassigned_staff_cannot(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("tower:teacher")).status_code, 403)
        self.assertEqual(self.set_access(self.client).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("tower:teacher")).status_code, 200)
        self.assertEqual(self.set_access(self.client).status_code, 200)
        self.assertEqual(self.set_access(self.client, self.ordinary_group).status_code, 400)

    def test_staff_cannot_grant_teacher_permission_in_admin_even_with_change_permission(self):
        self.client.force_login(self.staff)
        url = reverse("admin:core_teacher_change", args=[self.ordinary_teacher.pk])
        self.assertNotContains(self.client.get(url), 'name="can_manage_tower"')
        response = self.client.post(url, {
            "full_name": self.ordinary_teacher.full_name, "is_active": "on", "password": "",
            "can_manage_tower": "on", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        self.ordinary_teacher.refresh_from_db()
        self.assertFalse(self.ordinary_teacher.can_manage_tower)

    def test_superuser_can_grant_teacher_permission_through_admin(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:core_teacher_change", args=[self.ordinary_teacher.pk]), {
            "full_name": self.ordinary_teacher.full_name, "is_active": "on", "password": "",
            "can_manage_tower": "on", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        self.ordinary_teacher.refresh_from_db()
        self.assertTrue(self.ordinary_teacher.can_manage_tower)

    def test_staff_cannot_grant_class_access_through_admin(self):
        self.client.force_login(self.staff)
        url = reverse("admin:core_classgroup_change", args=[self.disabled_group.pk])
        self.assertNotContains(self.client.get(url), 'name="tower_enabled"')
        response = self.client.post(url, {
            "name": self.disabled_group.name, "owner": self.teacher.pk, "tower_enabled": "on", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        self.disabled_group.refresh_from_db()
        self.assertFalse(self.disabled_group.tower_enabled)

    def test_superuser_can_toggle_class_in_admin_and_guest_is_invalid(self):
        self.client.force_login(self.admin)
        url = reverse("admin:core_classgroup_change", args=[self.disabled_group.pk])
        data = {"name": self.disabled_group.name, "owner": self.teacher.pk, "tower_enabled": "on", "_save": "Save"}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.disabled_group.refresh_from_db()
        self.assertTrue(self.disabled_group.tower_enabled)
        data["is_guest"] = "on"
        self.assertEqual(self.client.post(url, data).status_code, 200)
        self.disabled_group.refresh_from_db()
        self.assertFalse(self.disabled_group.is_guest)
