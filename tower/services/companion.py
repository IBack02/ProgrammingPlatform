"""Account-wide generation gate; every network call runs outside DB transactions."""
import logging
import os
import time
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from openai import APIConnectionError, APITimeoutError

from core.models import ExamAttempt, GameParticipant, Student
from tower.companion_ai import generate_companion_reply
from tower.companion_contracts import COOLDOWN, FAILURE_RETRY_DELAY, RESERVATION_TTL
from tower.models import CompanionAccountState, CompanionMessage, CompanionRequest, HeroDefinition, PlayerCosmetic, TowerPlayer
from tower.permissions import eligible_student_classes
from .companion_delivery import SourceReservationError, acknowledge_learning_reply, store_learning_reply
from .companion_sources import _class_ids, authorized_student, current_source, select_learning_source, stored_source_url

logger = logging.getLogger(__name__)


def companion_enabled(student):
    if not settings.TOWER_COMPANION_ENABLED:
        return False
    classes = eligible_student_classes(student)
    allowed = settings.TOWER_COMPANION_CLASS_IDS
    return classes.filter(pk__in=allowed).exists() if allowed else classes.exists()


def _selected(student):
    player = TowerPlayer.objects.filter(student=student).first()
    slug = player.appearance.get("companion") if player else None
    owned = PlayerCosmetic.objects.filter(player=player, cosmetic_id=slug,
        cosmetic__slot="companion", cosmetic__acquisition="character_set").select_related("cosmetic__set_version__hero").first() if slug else None
    return owned.cosmetic if owned else None


def _quiet(student, surface, now):
    if surface in ("exam", "game"):
        return True
    classes = _class_ids(student)
    if ExamAttempt.objects.filter(student=student, status="in_progress", expires_at__gt=now,
            exam__status="running", exam__allowed_classes__pk__in=classes).exists():
        return True
    return GameParticipant.objects.filter(student=student, finished_at__isnull=True,
        round__status="running", round__class_group_id__in=classes, round__module__is_active=True,
        round__module__session__allowed_classes__pk__in=classes).exists()


def _identity(message):
    return message.source_kind, message.resource_type, message.resource_id, message.revision


def _message_data(student, message, now):
    if not message or not message.text or not message.hero_id:
        return None
    link = stored_source_url(student=student, message=message, now=now)
    if link is None:
        return None
    return {"id": str(message.pk), "text": message.text, "url": link,
            "hero_id": message.hero_id, "hero_name": HeroDefinition.objects.filter(pk=message.hero_id).values_list("name", flat=True).first() or message.hero_id,
            "saved_at": message.completed_at.isoformat(), "delivered": message.delivered_at is not None}


def companion_status(*, student, surface="learning", now=None):
    student = authorized_student(student)
    now = now or timezone.now()
    if not companion_enabled(student) or not _selected(student):
        return {"state": "inactive", "message": None, "next_available_at": None}
    if _quiet(student, surface, now):
        return {"state": "quiet", "message": None, "next_available_at": None}
    state = CompanionAccountState.objects.filter(student=student).select_related("active_message").first()
    latest = CompanionMessage.objects.filter(student=student, completed_at__isnull=False).order_by("-completed_at", "-pk").first()
    message = _message_data(student, latest, now)
    success = max(filter(None, [state.last_success_at if state else None, latest.completed_at if latest else None]), default=None)
    response = {"state": "empty", "message": message, "next_available_at": None}
    active = state.active_message if state else None
    if active and active.generation_status == "generating" and active.reserved_until > now:
        return {**response, "state": "generating", "next_available_at": active.reserved_until.isoformat()}
    if success and now < success + COOLDOWN:
        return {**response, "state": "cooldown", "next_available_at": (success + COOLDOWN).isoformat()}
    if state and state.retry_after and state.retry_after > now:
        return {**response, "state": "retry_wait", "next_available_at": state.retry_after.isoformat()}
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {**response, "state": "unavailable"}
    return {**response, "state": "ready" if select_learning_source(student=student, now=now) else "empty"}


@transaction.atomic
def _begin(student, request_id, language, surface, now):
    student = Student.objects.select_for_update().get(pk=student.pk)
    student = authorized_student(student)
    status = companion_status(student=student, surface=surface, now=now)
    if status["state"] in ("inactive", "quiet"):
        return None, False, status
    prior = CompanionRequest.objects.filter(student=student, request_id=request_id).select_related("message").first()
    if prior:
        message = prior.message
        return message, False, {**status, "message": _message_data(student, message, now),
            "error": message.failure_code or None, "replayed": True}
    state, _ = CompanionAccountState.objects.get_or_create(student=student)
    if status["state"] == "generating":
        CompanionRequest.objects.create(student=student, request_id=request_id, message=state.active_message)
        return state.active_message, False, {**status, "replayed": True}
    if status["state"] != "ready":
        return None, False, status
    source = select_learning_source(student=student, now=now)
    if source is None:
        return None, False, {**status, "state": "empty"}
    item = _selected(student)
    if state.active_message and state.active_message.generation_status == "generating":
        CompanionMessage.objects.filter(pk=state.active_message_id).update(generation_status="expired", failure_code="reservation_expired")
    message = CompanionMessage.objects.create(student=student, source_kind=source.kind,
        resource_type=source.resource_type, resource_id=source.resource_id, revision=source.revision,
        source_snapshot=source.as_dict(), reserved_until=now + RESERVATION_TTL, generation_status="generating",
        hero_id=item.set_version.hero_id, behavior_snapshot=item.set_version.behavior, language=language)
    state.active_message = message
    state.retry_after = None
    state.save(update_fields=["active_message", "retry_after"])
    CompanionRequest.objects.create(student=student, request_id=request_id, message=message)
    return message, True, status


@transaction.atomic
def _finish(student, message_id, result):
    Student.objects.select_for_update().get(pk=student.pk)
    student = authorized_student(student)
    now = timezone.now()
    state = CompanionAccountState.objects.select_related("active_message").get(student=student)
    message = state.active_message
    if (not message or message.pk != message_id or message.generation_status != "generating"
            or message.reserved_until <= now):
        raise SourceReservationError("reservation_expired")
    if not companion_enabled(student) or _quiet(student, "learning", now):
        raise SourceReservationError("generation_disabled")
    if not PlayerCosmetic.objects.filter(player__student=student, cosmetic__slot="companion",
            cosmetic__set_version__hero_id=message.hero_id).exists():
        raise SourceReservationError("companion_not_owned")
    saved = store_learning_reply(student=student, message_id=message.pk, text=result.text, now=now)
    saved.generation_status = "succeeded"
    saved.model, saved.tokens_in, saved.tokens_out = result.model, result.tokens_in, result.tokens_out
    saved.duration_ms, saved.provider_request_id = result.duration_ms, result.request_id[:200]
    saved.save(update_fields=["generation_status", "model", "tokens_in", "tokens_out", "duration_ms", "provider_request_id"])
    state.last_success_at, state.retry_after = saved.completed_at, None
    state.save(update_fields=["last_success_at", "retry_after"])


@transaction.atomic
def _fail(student, message_id, code, uncertain, duration_ms, metrics=None):
    Student.objects.select_for_update().get(pk=student.pk)
    state = CompanionAccountState.objects.select_related("active_message").get(student=student)
    message = state.active_message
    if not message or message.pk != message_id or message.generation_status != "generating":
        return
    now = timezone.now()
    message.generation_status = "uncertain" if uncertain else "failed"
    message.failure_code, message.duration_ms = code, duration_ms
    message.model = metrics.model if metrics else settings.TOWER_COMPANION_MODEL
    if metrics:
        message.tokens_in, message.tokens_out = metrics.tokens_in, metrics.tokens_out
        message.provider_request_id = metrics.request_id[:200]
    message.save(update_fields=["generation_status", "failure_code", "duration_ms", "model", "tokens_in", "tokens_out", "provider_request_id"])
    state.retry_after = max(now + FAILURE_RETRY_DELAY, message.reserved_until) if uncertain else now + FAILURE_RETRY_DELAY
    state.save(update_fields=["retry_after"])


def request_companion_reply(*, student, request_id, language, surface="learning"):
    try:
        key = uuid.UUID(str(request_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_request_id") from None
    if language not in ("ru", "kk", "en") or surface not in ("learning", "game", "exam"):
        raise ValueError("invalid_payload")
    message, launch, status = _begin(student, key, language, surface, timezone.now())
    if not launch:
        return status
    start = time.monotonic()
    try:
        result = generate_companion_reply(message)
        _finish(student, message.pk, result)
    except Exception as exc:
        uncertain = isinstance(exc, (APITimeoutError, APIConnectionError))
        code = "provider_unavailable" if uncertain else "generation_failed"
        if isinstance(exc, SourceReservationError):
            code = str(exc)
        _fail(student, message.pk, code, uncertain, round((time.monotonic() - start) * 1000), getattr(exc, "metrics", None))
        logger.warning("Companion generation failed: message=%s type=%s", message.pk, type(exc).__name__)
        return {**companion_status(student=student, surface=surface), "error": code}
    logger.info("Companion generation saved: message=%s model=%s input=%s output=%s ms=%s",
                message.pk, result.model, result.tokens_in, result.tokens_out, result.duration_ms)
    return companion_status(student=student, surface=surface)


def acknowledge_companion(*, student, message_id, surface="learning"):
    student = authorized_student(student)
    if not companion_enabled(student) or _quiet(student, surface, timezone.now()):
        raise SourceReservationError("generation_disabled")
    acknowledge_learning_reply(student=student, message_id=message_id)
