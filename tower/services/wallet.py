"""Atomic money operations. No generic client-controlled debit endpoint is exposed."""

import hashlib
import json
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone

from core.models import ClassGroup, Student, Teacher
from tower.models import MAX_BALANCE, GameOperation, TowerAuditEvent, TowerPlayer, Wallet, WalletEntry
from tower.permissions import eligible_student_classes, tower_is_enabled


class WalletError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def _reason(value):
    if not isinstance(value, str):
        raise WalletError("reason_required")
    value = " ".join(value.split())
    if not value or len(value) > 240:
        raise WalletError("reason_required")
    return value


def _request_id(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise WalletError("invalid_request_id") from None


def _positive_amount(value, maximum=MAX_BALANCE):
    if type(value) is not int or not 0 < value <= maximum:
        raise WalletError("invalid_amount")
    return value


def _fresh_admin(admin):
    if not tower_is_enabled() or not admin:
        raise PermissionDenied
    operator = get_user_model().objects.filter(pk=admin.pk, is_active=True, is_superuser=True).first()
    if not operator:
        raise PermissionDenied
    return operator


def _fresh_teacher(teacher):
    if not tower_is_enabled() or not teacher:
        raise PermissionDenied
    operator = Teacher.objects.filter(pk=teacher.pk, is_active=True, can_manage_tower=True).first()
    if not operator:
        raise PermissionDenied
    return operator


def _locked_wallet(student_id):
    # Lock a stable existing row before lazy creation; also serializes the first
    # two requests for a student who does not have a TowerPlayer/Wallet yet.
    student = Student.objects.select_for_update().filter(pk=student_id).first()
    if not student:
        raise Http404
    if not eligible_student_classes(student).exists():
        raise PermissionDenied
    player, _ = TowerPlayer.objects.get_or_create(student=student)
    wallet, _ = Wallet.objects.get_or_create(player=player)
    return Wallet.objects.select_for_update().get(pk=wallet.pk)


@transaction.atomic
def student_wallet(student):
    return _locked_wallet(student.pk)


def _perform(*, student_id, currency, delta, reason, request_id, action,
             actor_scope, source, teacher=None, admin=None, group=None, reference=""):
    if currency not in WalletEntry.Currency.values:
        raise WalletError("invalid_currency")
    reason = _reason(reason)
    request_id = _request_id(request_id)
    if not isinstance(reference, str) or len(reference) > 120:
        raise WalletError("invalid_reference")
    payload = {
        "student_id": student_id, "class_id": group.pk if group else None,
        "currency": currency, "delta": delta, "reason": reason, "reference": reference,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    identity = {"actor_scope": actor_scope, "action": action, "request_id": request_id}

    def replay(operation):
        if operation.payload_hash != digest:
            raise WalletError("request_conflict", 409)
        return {**operation.result, "replayed": True}

    # Authorization is performed before this lookup as well as before a write.
    existing = GameOperation.objects.filter(**identity).first()
    if existing:
        return replay(existing)
    wallet = _locked_wallet(student_id)
    existing = GameOperation.objects.filter(**identity).first()
    if existing:
        return replay(existing)
    try:
        with transaction.atomic():
            operation = GameOperation.objects.create(
                **identity, player=wallet.player, payload_hash=digest,
                actor_teacher=teacher, actor_admin=admin,
            )
    except IntegrityError:
        # Same actor/request concurrently targeting another player must conflict,
        # not create a second award. The unique key deliberately includes actor
        # scope, not only player: changing the target cannot reuse a request ID.
        return replay(GameOperation.objects.get(**identity))

    before = getattr(wallet, currency)
    after = before + delta
    if after < 0:
        raise WalletError("insufficient_funds", 409)
    if after > MAX_BALANCE:
        raise WalletError("balance_limit", 409)
    setattr(wallet, currency, after)
    wallet.updated_at = timezone.now()
    wallet.save(update_fields=[currency, "updated_at"])
    entry = WalletEntry.objects.create(
        wallet=wallet, operation=operation, currency=currency, delta=delta,
        balance_after=after, reason=reason, source=source,
        class_group=group, reference=reference,
    )
    TowerAuditEvent.objects.create(
        event_type=source, actor_teacher=teacher, actor_admin=admin,
        student_id=student_id, class_group=group, entry=entry,
        metadata={"currency": currency, "before": before, "after": after, "delta": delta},
    )
    operation.result = {
        "entry_id": entry.pk, "request_id": str(request_id),
        "currency": currency, "delta": delta, "balance_after": after,
    }
    operation.save(update_fields=["result"])
    return {**operation.result, "replayed": False}


@transaction.atomic
def award_coins(*, student_id, class_id, amount, reason, request_id, teacher=None, admin=None):
    amount = _positive_amount(amount, settings.TOWER_MANUAL_AWARD_LIMIT)
    if bool(teacher) == bool(admin):
        raise PermissionDenied
    if admin:
        admin = _fresh_admin(admin)
        scope = f"admin:{admin.pk}"
        groups = ClassGroup.objects.all()
    else:
        teacher = _fresh_teacher(teacher)
        scope = f"teacher:{teacher.pk}"
        groups = ClassGroup.objects.filter(owner=teacher)
    group = groups.filter(pk=class_id).first()
    if not group:
        raise Http404
    if not (group.tower_enabled and not group.is_guest and group.owner
            and group.owner.is_active and group.owner.can_manage_tower):
        raise PermissionDenied
    if not Student.objects.filter(
        Q(class_group=group) | Q(class_memberships__class_group=group),
        pk=student_id, is_active=True, class_group__is_guest=False, guest_lease_token__isnull=True,
    ).exists():
        raise Http404
    return _perform(
        student_id=student_id, currency=WalletEntry.Currency.NISCOIN, delta=amount,
        reason=reason, request_id=request_id, action="manual_award", actor_scope=scope,
        source=WalletEntry.Source.MANUAL, teacher=teacher, admin=admin, group=group,
    )


@transaction.atomic
def spend_coins(*, student, amount, reason, reference, request_id):
    """Internal building block for later purchases; product fulfilment must share
    this transaction. Never accept a price or a successful purchase from JS.
    """
    _positive_amount(amount)
    fresh = Student.objects.filter(pk=student.pk).first()
    if not eligible_student_classes(fresh).exists():
        raise PermissionDenied
    return _perform(
        student_id=fresh.pk, currency=WalletEntry.Currency.NISCOIN, delta=-amount,
        reason=reason, reference=reference, request_id=request_id,
        action="spend", actor_scope=f"student:{fresh.pk}", source=WalletEntry.Source.PURCHASE,
    )


@transaction.atomic
def adjust_wallet(*, admin, student_id, currency, delta, reason, request_id):
    admin = _fresh_admin(admin)
    if type(delta) is not int or delta == 0 or abs(delta) > settings.TOWER_ADMIN_ADJUSTMENT_LIMIT:
        raise WalletError("invalid_amount")
    return _perform(
        student_id=student_id, currency=currency, delta=delta, reason=reason,
        request_id=request_id, action="adjustment", actor_scope=f"admin:{admin.pk}",
        source=WalletEntry.Source.CORRECTION, admin=admin,
    )
