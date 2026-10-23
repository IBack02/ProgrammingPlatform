import uuid

from django.contrib.auth import get_user_model
from django.test import Client

from core.models import ClassGroup, Student, Teacher
from core.security import auth_version
from tower.services.wallet import award_coins


class WalletFixtures:
    @classmethod
    def create_accounts(cls):
        cls.teacher = Teacher.objects.create(full_name="Wallet teacher", pin_hash="unused", can_manage_tower=True)
        cls.other_teacher = Teacher.objects.create(full_name="Other teacher", pin_hash="unused", can_manage_tower=True)
        cls.group = ClassGroup.objects.create(name="Wallet class", owner=cls.teacher, tower_enabled=True)
        cls.other_group = ClassGroup.objects.create(name="Other class", owner=cls.other_teacher, tower_enabled=True)
        cls.student = Student.objects.create(full_name="Wallet student", class_group=cls.group, pin_hash="unused")
        cls.other_student = Student.objects.create(full_name="Other student", class_group=cls.other_group, pin_hash="unused")
        cls.admin = get_user_model().objects.create_superuser(username="wallet-admin", password="password")
        cls.staff = get_user_model().objects.create_user(username="wallet-staff", password="password", is_staff=True)

    def award(self, **kwargs):
        values = dict(teacher=self.teacher, student_id=self.student.pk, class_id=self.group.pk,
                      amount=100, reason="Quiz prize", request_id=uuid.uuid4())
        values.update(kwargs)
        return award_coins(**values)

    def account_client(self, account=None, *, csrf=False):
        account = account or self.student
        client = Client(enforce_csrf_checks=csrf)
        session = client.session
        prefix = "teacher" if isinstance(account, Teacher) else "student"
        session.update({f"{prefix}_id": account.pk, f"{prefix}_auth_version": auth_version(account.pin_hash)})
        if prefix == "student":
            session["student_class_id"] = account.class_group_id
        session.save()
        return client
