"""Durable source reservations and delivery receipts, ready for the generator."""
import uuid

from django.db import transaction
from django.utils import timezone

from core.models import Student
from tower.companion_contracts import REPLY_MAX_CHARACTERS, RESERVATION_TTL
from tower.models import CompanionMessage
from .companion_sources import authorized_student, current_source, select_learning_source


class SourceReservationError(ValueError):
    pass


def _identity(message):
    return message.source_kind, message.resource_type, message.resource_id, message.revision


def _locked_student(student):
    row = Student.objects.select_for_update().get(pk=student.pk)
    return authorized_student(row)


def _latest(student):
    return CompanionMessage.objects.filter(student=student, delivered_at__isnull=True).order_by("-created_at", "-pk").first()


def _owned_message(student, message_id):
    try:
        key = uuid.UUID(str(message_id))
    except (TypeError, ValueError, AttributeError):
        raise SourceReservationError("invalid_message") from None
    message = CompanionMessage.objects.filter(student=student, pk=key).first()
    if not message:
        raise SourceReservationError("message_not_found")
    return message


@transaction.atomic
def reserve_learning_source(*, student, now=None):
    student = _locked_student(student)
    now = now or timezone.now()
    existing = _latest(student)
    if existing and (existing.text or existing.reserved_until > now):
        if current_source(student=student, identity=_identity(existing), now=now):
            return existing
    source = select_learning_source(student=student, now=now)
    if source is None:
        return None
    return CompanionMessage.objects.create(student=student, source_kind=source.kind,
        resource_type=source.resource_type, resource_id=source.resource_id, revision=source.revision,
        source_snapshot=source.as_dict(), reserved_until=now + RESERVATION_TTL)


@transaction.atomic
def store_learning_reply(*, student, message_id, text, now=None):
    """Only trusted server code calls this after validating generated content."""
    student = _locked_student(student)
    now = now or timezone.now()
    message = _owned_message(student, message_id)
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > REPLY_MAX_CHARACTERS:
        raise SourceReservationError("invalid_reply")
    text = text.strip()
    if message.text:
        if text != message.text:
            raise SourceReservationError("reply_conflict")
        if not current_source(student=student, identity=_identity(message), now=now):
            raise SourceReservationError("source_changed")
        return message
    latest = _latest(student)
    if message.reserved_until <= now or not latest or latest.pk != message.pk:
        raise SourceReservationError("reservation_expired")
    if not current_source(student=student, identity=_identity(message), now=now):
        raise SourceReservationError("source_changed")
    message.text, message.completed_at = text, now
    message.save(update_fields=["text", "completed_at"])
    return message


@transaction.atomic
def acknowledge_learning_reply(*, student, message_id, now=None):
    """A saved reply consumes feedback only when the client confirms display."""
    student = _locked_student(student)
    now = now or timezone.now()
    message = _owned_message(student, message_id)
    if message.delivered_at:
        return message
    if not message.text:
        raise SourceReservationError("reply_not_saved")
    if not current_source(student=student, identity=_identity(message), now=now):
        raise SourceReservationError("source_changed")
    message.delivered_at = now
    message.save(update_fields=["delivered_at"])
    return message
