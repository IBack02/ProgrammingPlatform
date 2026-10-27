"""Account-scoped companion rules, independent of HTTP, ORM and AI providers."""
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class SourceKind(StrEnum):
    UNFINISHED = "unfinished"
    TEACHER_FEEDBACK = "teacher_feedback"
    LESSON_FACT = "lesson_fact"


SOURCE_PRIORITY = (SourceKind.UNFINISHED, SourceKind.TEACHER_FEEDBACK, SourceKind.LESSON_FACT)
COOLDOWN = timedelta(minutes=30)
RESERVATION_TTL = timedelta(seconds=90)
FAILURE_RETRY_DELAY = timedelta(seconds=30)
REPLY_SENTENCES = (2, 4)
REPLY_MAX_CHARACTERS = 600


class CompanionStatus(StrEnum):
    INACTIVE = "inactive"
    READY = "ready"
    GENERATING = "generating"
    COOLDOWN = "cooldown"
    EMPTY = "empty"
    RETRY_WAIT = "retry_wait"


def _aware(value):
    if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
        raise ValueError("Companion timestamps must include a timezone.")
    return value


@dataclass(frozen=True)
class AccountAvailability:
    status: CompanionStatus
    next_available_at: datetime | None = None

    @property
    def show_notification(self):
        return self.status == CompanionStatus.READY


def account_availability(*, now, enabled, companion_selected, has_source,
                         last_success_at=None, reservation_expires_at=None, retry_after=None):
    """Caller supplies ACCOUNT state; changing the selected character cannot reset it.

    last_success_at means a validated response committed to durable storage.
    An existing stored response can always be reread without this generation gate.
    """
    for value in (now, last_success_at, reservation_expires_at, retry_after):
        _aware(value)
    if now is None:
        raise ValueError("The current time is required.")
    if not enabled or not companion_selected:
        return AccountAvailability(CompanionStatus.INACTIVE)
    if reservation_expires_at and now < reservation_expires_at:
        return AccountAvailability(CompanionStatus.GENERATING)
    if last_success_at and now < last_success_at + COOLDOWN:
        return AccountAvailability(CompanionStatus.COOLDOWN, last_success_at + COOLDOWN)
    if retry_after and now < retry_after:
        return AccountAvailability(CompanionStatus.RETRY_WAIT, retry_after)
    return AccountAvailability(CompanionStatus.READY if has_source else CompanionStatus.EMPTY)


def feedback_revision(text):
    """Whitespace-only edits do not make an already considered comment new."""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceCandidate:
    kind: SourceKind
    source_id: int
    revision: str
    last_presented_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.kind, SourceKind) or type(self.source_id) is not int or self.source_id <= 0:
            raise ValueError("A candidate needs a known kind and a positive resource ID.")
        if not isinstance(self.revision, str) or not self.revision:
            raise ValueError("A source revision is required.")
        _aware(self.last_presented_at)

    @property
    def identity(self):
        # A separate resource with the same comment text remains a separate source.
        return self.kind, self.source_id, self.revision


def choose_source(candidates, *, considered_feedback=frozenset()):
    """Inputs MUST already be access-checked and selected by the learning service.

    Enforce priority and rotate within it. Stage 5 supplies actual ORM candidates
    and randomizes eligible fact modules/blocks before supplying a candidate.
    """
    candidates = tuple(candidates)
    for kind in SOURCE_PRIORITY:
        eligible = [candidate for candidate in candidates if candidate.kind == kind
                    and not (kind == SourceKind.TEACHER_FEEDBACK and candidate.identity in considered_feedback)]
        if eligible:
            return min(eligible, key=lambda item: (item.last_presented_at is not None,
                       item.last_presented_at.timestamp() if item.last_presented_at else 0, item.source_id))
    return None
