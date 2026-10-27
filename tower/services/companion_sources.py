"""Access-checked learning sources. This module never calls an AI provider."""
import hashlib
import json
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db.models import F, Max, Q
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Exam, ExamAnswer, ExamAttempt, GameModule, Session, SessionTask, Student, StudentClassMembership,
    StudentSession, StudentTaskProgress, StudentTheoryQuizAttempt, Submission, TheoryMaterialBlock,
    TheoryMaterialModule, TheoryQuizModule,
)
from tower.companion_contracts import SourceKind, feedback_revision
from tower.models import CompanionLearningVisit, CompanionMessage
from tower.permissions import eligible_student_classes

MAX_TEXT = 2400
MAX_COMMENT = 1600
MODULE_MODELS = {"coding_task": SessionTask, "theory_quiz": TheoryQuizModule,
                 "theory_material": TheoryMaterialModule, "game": GameModule}


def _revision(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _text(value, limit=MAX_TEXT):
    return " ".join((value or "").split())[:limit]


def _session_active(session, now):
    return (session.status == Session.Status.RUNNING and (not session.starts_at or session.starts_at <= now)
            and (not session.ends_at or now <= session.ends_at))


@dataclass(frozen=True)
class LearningSource:
    kind: str
    resource_type: str
    resource_id: int
    revision: str
    context: dict
    url: str

    @property
    def identity(self):
        return self.kind, self.resource_type, self.resource_id, self.revision

    def as_dict(self):
        return {"kind": self.kind, "resource_type": self.resource_type, "resource_id": self.resource_id,
                "revision": self.revision, "context": self.context, "url": self.url}


def authorized_student(student):
    current = Student.objects.select_related("class_group").filter(pk=student.pk, is_active=True).first()
    if current is None or not eligible_student_classes(current).exists():
        raise PermissionDenied
    return current


def _class_ids(student):
    return {student.class_group_id, *StudentClassMembership.objects.filter(student=student).values_list("class_group_id", flat=True)}


def record_learning_visit(*, student_id, module, module_type):
    """Called after the learning endpoint has authorized a genuine module open."""
    if module_type not in MODULE_MODELS or not isinstance(module, MODULE_MODELS[module_type]):
        raise ValueError("Module type does not match its resource.")
    student = Student.objects.select_related("class_group").filter(pk=student_id, is_active=True).first()
    if not student or not eligible_student_classes(student).exists():
        return
    session = module.session
    now = timezone.now()
    if (session.status == Session.Status.DRAFT or not session.allowed_classes.filter(pk__in=_class_ids(student)).exists()
            or (hasattr(module, "is_active") and not module.is_active)):
        return
    if not _session_active(session, now) and not StudentSession.objects.filter(student=student, session=session).exists():
        return
    StudentSession.objects.get_or_create(student=student, session=session,
        defaults={"started_at": now, "last_seen_at": now})
    CompanionLearningVisit.objects.update_or_create(student=student, module_type=module_type, resource_id=module.pk,
        defaults={"session": session, "last_visited_at": now}, create_defaults={
            "session": session, "first_visited_at": now, "last_visited_at": now})


def _lesson_url(session_id, resource_id, module_type):
    return reverse("student_portal_page") + "?" + urlencode({"session_id": session_id, "task_id": resource_id, "module_type": module_type})


def _history(student):
    delivered = CompanionMessage.objects.filter(student=student, delivered_at__isnull=False)
    history = { (row["source_kind"], row["resource_type"], row["resource_id"], row["revision"]): row["last"]
        for row in delivered.values("source_kind", "resource_type", "resource_id", "revision").annotate(last=Max("delivered_at")) }
    return history


def _lesson_state(student, now):
    classes = _class_ids(student)
    sessions = {session.pk: session for session in Session.objects.filter(allowed_classes__id__in=classes)
                .exclude(status=Session.Status.DRAFT).distinct()}
    attendance = {row.session_id: row for row in StudentSession.objects.filter(student=student, session_id__in=sessions)}
    sessions = {sid: session for sid, session in sessions.items() if _session_active(session, now) or sid in attendance}
    visits = list(CompanionLearningVisit.objects.filter(student=student, session_id__in=sessions).order_by("last_visited_at", "pk"))
    # Legacy attendance remains useful until a student has real visit timestamps.
    visited_at = {sid: row.last_seen_at or row.started_at for sid, row in attendance.items() if row.last_seen_at or row.started_at}
    real_times = {}
    for row in visits:
        real_times[row.session_id] = max(real_times.get(row.session_id, row.last_visited_at), row.last_visited_at)
    visited_at.update(real_times)
    real_latest = max(visits, key=lambda row: (row.last_visited_at, row.pk), default=None)
    latest_session = real_latest.session_id if real_latest else max(visited_at, key=lambda sid: (visited_at[sid], sid), default=None)
    return sessions, attendance, visited_at, latest_session


def _unfinished(student, now, state):
    sessions, attendance, visits, latest_session = state
    progress = {row.task_id: row for row in StudentTaskProgress.objects.filter(
        student_session__student=student, student_session__session_id__in=sessions,
        task__session_id=F("student_session__session_id"))}
    quizzes_done = set(StudentTheoryQuizAttempt.objects.filter(student_session__student=student,
        module__session_id=F("student_session__session_id")).values_list("module_id", flat=True))
    accepted_tasks = set(Submission.objects.filter(progress__student_session__student=student,
        progress__task__session_id=F("progress__student_session__session_id"),
        verdict=Submission.Verdict.ACCEPTED).values_list("progress__task_id", flat=True))
    completed_sessions = {row.task.session_id for row in StudentTaskProgress.objects.filter(
        student_session__student=student, task__session_id=F("student_session__session_id"))
        .filter(Q(status__in=["solved", "locked"]) | Q(solved_at__isnull=False)).select_related("task")}
    completed_sessions.update(StudentTheoryQuizAttempt.objects.filter(student_session__student=student,
        module__session_id=F("student_session__session_id")).values_list("module__session_id", flat=True))
    completed_sessions.update(SessionTask.objects.filter(pk__in=accepted_tasks).values_list("session_id", flat=True))
    actionable = {sid for sid, session in sessions.items() if sid in visits and _session_active(session, now)
        and not (attendance.get(sid) and attendance[sid].finished_at)
        and (sid in completed_sessions or (latest_session is not None and sid != latest_session))}
    candidates = []
    for task in SessionTask.objects.filter(session_id__in=actionable).order_by("session_id", "position", "pk"):
        row = progress.get(task.pk)
        if task.pk in accepted_tasks or (row and (row.status in ("solved", "locked") or row.solved_at)):
            continue
        context = {"session_id": task.session_id, "lesson": _text(sessions[task.session_id].title, 200),
                   "title": _text(task.title, 200), "statement": _text(task.statement),
                   "status": "in_progress" if row and row.opened_at else "not_started"}
        candidates.append(LearningSource(SourceKind.UNFINISHED, "coding_task", task.pk,
            _revision([task.title, task.statement, task.programming_language, context["status"]]), context,
            _lesson_url(task.session_id, task.pk, "coding_task")))
    for module in TheoryQuizModule.objects.filter(session_id__in=actionable, is_active=True).order_by("session_id", "position", "pk"):
        if module.pk in quizzes_done:
            continue
        context = {"session_id": module.session_id, "lesson": _text(sessions[module.session_id].title, 200),
                   "title": _text(module.title, 200), "instructions": _text(module.instructions), "status": "not_submitted"}
        candidates.append(LearningSource(SourceKind.UNFINISHED, "theory_quiz", module.pk,
            _revision([module.title, module.instructions, module.updated_at]), context,
            _lesson_url(module.session_id, module.pk, "theory_quiz")))
    attempts = {row.exam_id: row for row in ExamAttempt.objects.filter(student=student)}
    exams = Exam.objects.filter(status=Exam.Status.RUNNING, allowed_classes__id__in=_class_ids(student)).distinct().order_by("pk")
    for exam in exams:
        attempt = attempts.get(exam.pk)
        if attempt and (attempt.status != ExamAttempt.Status.IN_PROGRESS or attempt.expires_at <= now):
            continue
        context = {"title": _text(exam.title, 200), "topic": _text(exam.topic, 255),
                   "status": "in_progress" if attempt else "not_started", "exam_id": exam.pk}
        if attempt:
            context["attempt_id"] = attempt.pk
        candidates.append(LearningSource(SourceKind.UNFINISHED, "exam", exam.pk,
            _revision([exam.pk, exam.updated_at, context["status"], attempt.pk if attempt else None]), context,
            reverse("student_exams_page") + "?" + urlencode({"exam_id": exam.pk})))
    return candidates


def _feedback(student, history):
    rows = ExamAnswer.objects.filter(attempt__student=student,
        attempt__status__in=[ExamAttempt.Status.SUBMITTED, ExamAttempt.Status.EXPIRED],
        question__exam_id=F("attempt__exam_id"), awarded_score__isnull=False,
        awarded_score__lt=F("question__max_score")).exclude(teacher_feedback="").select_related("attempt__exam", "question").order_by("pk")
    result = []
    for answer in rows:
        comment = _text(answer.teacher_feedback, MAX_COMMENT)
        if not comment:
            continue
        revision = feedback_revision(answer.teacher_feedback)
        identity = (SourceKind.TEACHER_FEEDBACK, "exam_answer", answer.pk, revision)
        if identity in history:
            continue
        context = {"exam_id": answer.attempt.exam_id, "attempt_id": answer.attempt_id,
            "title": _text(answer.attempt.exam.title, 200), "question": _text(answer.question.prompt, 1200),
            "comment": comment, "score": str(answer.awarded_score), "max_score": str(answer.question.max_score)}
        result.append(LearningSource(SourceKind.TEACHER_FEEDBACK, "exam_answer", answer.pk, revision, context,
            reverse("student_exam_result_page", args=[answer.attempt_id]) + f"#question-{answer.question_id}"))
    return result


def _facts(student, state):
    sessions, attendance, visits, latest_session = state
    if latest_session is None:
        return []
    blocks = TheoryMaterialBlock.objects.filter(module__session_id=latest_session, module__is_active=True,
        block_type=TheoryMaterialBlock.BlockType.TEXT).exclude(content="").select_related("module").order_by("pk")
    result = []
    for block in blocks:
        text = _text(block.content)
        if not text:
            continue
        context = {"session_id": latest_session, "lesson": _text(sessions[latest_session].title, 200),
                   "module_id": block.module_id, "title": _text(block.module.title, 200),
                   "topic": _text(block.module.topic, 255), "text": text}
        result.append(LearningSource(SourceKind.LESSON_FACT, "theory_block", block.pk,
            _revision([block.module.title, block.module.topic, block.content]), context,
            _lesson_url(latest_session, block.module_id, "theory_material") + f"#theory-block-{block.pk}"))
    return result


def source_candidates(*, student, now=None, include_considered_feedback=False):
    student = authorized_student(student)
    now = now or timezone.now()
    state = _lesson_state(student, now)
    history = _history(student)
    return _unfinished(student, now, state), _feedback(student, {} if include_considered_feedback else history), _facts(student, state), history


def select_learning_source(*, student, now=None, chooser=None):
    """Read-only: checking readiness neither consumes a source nor reserves it."""
    work, feedback, facts, history = source_candidates(student=student, now=now)
    for candidates in (work, feedback):
        if candidates:
            # Round-robin across resource types with equal numeric IDs too.
            return min(candidates, key=lambda source: (source.identity in history,
                history[source.identity].timestamp() if source.identity in history else 0,
                source.context.get("session_id", 0), source.resource_type, source.resource_id))
    if not facts:
        return None
    # Avoid the last displayed fact, then choose a module uniformly and a block uniformly.
    previous = max(((identity, at) for identity, at in history.items() if identity[0] == SourceKind.LESSON_FACT),
                   key=lambda row: row[1], default=None)
    if previous and len(facts) > 1:
        facts = [source for source in facts if source.identity != previous[0]]
    modules = sorted({source.context["module_id"] for source in facts})
    choose = chooser or secrets.choice
    module_id = choose(modules)
    return choose([source for source in facts if source.context["module_id"] == module_id])


def current_source(*, student, identity, now=None):
    """Revalidate reservations before saving or delivering their reply."""
    work, feedback, facts, history = source_candidates(student=student, now=now, include_considered_feedback=True)
    return next((source for source in (*work, *feedback, *facts) if source.identity == identity), None)


def stored_source_url(*, student, message, now=None):
    """A saved reply remains readable after progress changes, while access is checked again."""
    student = authorized_student(student)
    now = now or timezone.now()
    if message.resource_type == "exam_answer":
        answer = ExamAnswer.objects.filter(pk=message.resource_id, attempt__student=student,
            attempt__status__in=["submitted", "expired"], question__exam_id=F("attempt__exam_id")).first()
        return reverse("student_exam_result_page", args=[answer.attempt_id]) + f"#question-{answer.question_id}" if answer else None
    if message.resource_type == "exam":
        exam = Exam.objects.filter(pk=message.resource_id).filter(
            Q(status="running", allowed_classes__pk__in=_class_ids(student)) | Q(attempts__student=student)).first()
        return reverse("student_exams_page") + "?" + urlencode({"exam_id": exam.pk}) if exam else None
    resource = None
    if message.resource_type in ("coding_task", "theory_quiz"):
        resource = MODULE_MODELS[message.resource_type].objects.select_related("session").filter(pk=message.resource_id).first()
        if resource and hasattr(resource, "is_active") and not resource.is_active:
            return None
        module_type, module_id = message.resource_type, resource.pk if resource else None
    elif message.resource_type == "theory_block":
        block = TheoryMaterialBlock.objects.select_related("module__session").filter(pk=message.resource_id, module__is_active=True).first()
        resource = block.module if block else None
        module_type, module_id = "theory_material", resource.pk if resource else None
    if resource is None:
        return None
    session = resource.session
    if session.status == "draft" or not session.allowed_classes.filter(pk__in=_class_ids(student)).exists():
        return None
    if not _session_active(session, now) and not StudentSession.objects.filter(student=student, session=session).exists():
        return None
    link = _lesson_url(session.pk, module_id, module_type)
    return link + f"#theory-block-{message.resource_id}" if message.resource_type == "theory_block" else link
