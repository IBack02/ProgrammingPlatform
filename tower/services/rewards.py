"""Class reward rules and server-verified, once-only learning events."""
import hashlib
import json
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404

from core.models import ClassGroup, GameModule, GameRound, Student
from tower.models import GameOperation, RewardBatch, RewardPolicy, TowerAuditEvent, WalletEntry
from tower.permissions import eligible_student_classes, tower_is_enabled
from .wallet import (
    WalletError, _fresh_admin, _fresh_teacher, _locked_wallet, _perform,
    _positive_amount, _reason, _request_id, award_coins,
)


POLICY_FIELDS = ("race_first", "race_second", "race_third", "task_solved", "page_opened")


def _operator_group(*, class_id, teacher=None, admin=None):
    if bool(teacher) == bool(admin):
        raise PermissionDenied
    if admin:
        admin = _fresh_admin(admin)
        groups, scope = ClassGroup.objects.all(), f"admin:{admin.pk}"
    else:
        teacher = _fresh_teacher(teacher)
        groups, scope = ClassGroup.objects.filter(owner=teacher), f"teacher:{teacher.pk}"
    group = groups.select_for_update().filter(
        pk=class_id, tower_enabled=True, is_guest=False,
        owner__is_active=True, owner__can_manage_tower=True,
    ).first()
    if not group:
        raise Http404
    return group, teacher, admin, scope


@transaction.atomic
def save_policy(*, class_id, values, teacher=None, admin=None):
    group, teacher, admin, _ = _operator_group(class_id=class_id, teacher=teacher, admin=admin)
    if set(values) != set(POLICY_FIELDS) or any(
        type(value) is not int or not 0 <= value <= settings.TOWER_MANUAL_AWARD_LIMIT
        for value in values.values()
    ):
        raise WalletError("invalid_amount")
    if not values["race_first"] >= values["race_second"] >= values["race_third"]:
        raise WalletError("invalid_podium")
    policy, _ = RewardPolicy.objects.get_or_create(class_group=group)
    before = {field: getattr(policy, field) for field in POLICY_FIELDS}
    for field, value in values.items():
        setattr(policy, field, value)
    policy.save()
    TowerAuditEvent.objects.create(event_type="reward_policy", class_group=group,
        actor_teacher=teacher, actor_admin=admin, metadata={"before": before, "after": values})
    return policy


@transaction.atomic
def award_batch(*, class_id, student_ids, amount, reason, request_id, teacher=None, admin=None):
    group, teacher, admin, scope = _operator_group(class_id=class_id, teacher=teacher, admin=admin)
    amount = _positive_amount(amount, settings.TOWER_MANUAL_AWARD_LIMIT)
    reason, request_id = _reason(reason), _request_id(request_id)
    if not student_ids or len(student_ids) > 500 or any(type(pk) is not int or pk <= 0 for pk in student_ids):
        raise WalletError("invalid_identifier")
    student_ids = sorted(set(student_ids))
    members = Student.objects.filter(
        Q(class_group=group) | Q(class_memberships__class_group=group),
        pk__in=student_ids, is_active=True, class_group__is_guest=False, guest_lease_token__isnull=True,
    ).values_list("pk", flat=True).distinct()
    if set(members) != set(student_ids):
        raise Http404
    digest = hashlib.sha256(json.dumps([class_id, student_ids, amount, reason]).encode()).hexdigest()
    existing = RewardBatch.objects.filter(actor_scope=scope, request_id=request_id).first()
    if existing:
        if existing.payload_hash != digest:
            raise WalletError("request_conflict", 409)
        return {"count": len(student_ids), "replayed": True}
    try:
        with transaction.atomic():
            RewardBatch.objects.create(actor_scope=scope, request_id=request_id, payload_hash=digest)
    except IntegrityError:
        existing = RewardBatch.objects.get(actor_scope=scope, request_id=request_id)
        if existing.payload_hash != digest:
            raise WalletError("request_conflict", 409) from None
        return {"count": len(student_ids), "replayed": True}
    for pk in student_ids:
        award_coins(student_id=pk, class_id=class_id, amount=amount, reason=reason,
            request_id=uuid.uuid5(request_id, str(pk)), teacher=teacher, admin=admin)
    return {"count": len(student_ids), "replayed": False}


@transaction.atomic
def learning_reward(*, student_id, class_id, session_id, event, resource_id, title, place=None):
    if not tower_is_enabled() or event not in ("task", "quiz", "page", "race"):
        return None
    student = Student.objects.select_related("class_group").filter(pk=student_id).first()
    group = eligible_student_classes(student).filter(pk=class_id, sessions__pk=session_id).first()
    if not group:
        return None
    policy = RewardPolicy.objects.filter(class_group=group).first()
    field = {"task": "task_solved", "quiz": "task_solved", "page": "page_opened"}.get(event)
    if event == "race":
        field = {1: "race_first", 2: "race_second", 3: "race_third"}.get(place)
    amount = getattr(policy, field, 0) if policy and field else 0
    if not amount:
        return None
    # Global per learner/resource, including when switching between shared classes.
    key = uuid.uuid5(uuid.NAMESPACE_URL, f"tower:{student_id}:{event}:{resource_id}")
    scope = f"learning:{student_id}"
    _locked_wallet(student_id)
    if GameOperation.objects.filter(actor_scope=scope, action="learning_reward", request_id=key).exists():
        return None
    labels = {"task": "Решение задачи", "quiz": "Тест без ошибок", "page": "Открытие страницы", "race": f"Гонка · место {place}"}
    return _perform(student_id=student_id, currency=WalletEntry.Currency.NISCOIN, delta=amount,
        reason=f"{labels[event]}: {title}"[:240], request_id=key, action="learning_reward",
        actor_scope=scope, source=WalletEntry.Source.LEARNING, teacher=group.owner, group=group,
        reference=f"{event}:{resource_id}")


def reward_finished_race(round_obj):
    if (round_obj.status != GameRound.Status.FINISHED
            or round_obj.module.rubric != GameModule.Rubric.MIND_RACE or not round_obj.total_prompts):
        return
    # A teacher ending a race ranks unfinished players too. Only actual finishers earn prizes.
    for participant in round_obj.participants.filter(finish_place__in=[1, 2, 3], progress__gte=round_obj.total_prompts).order_by("student_id"):
        learning_reward(student_id=participant.student_id, class_id=round_obj.class_group_id,
            session_id=round_obj.module.session_id, event="race", resource_id=round_obj.pk,
            title=round_obj.module.title, place=participant.finish_place)
