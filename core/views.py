import hashlib
import json
import random
import re
from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils import timezone as dj_tz
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST


from .judge0_client import create_batch_submissions, wait_batch
from .models import (
    ActivityAggregate,
    AiAssistMessage,
    ClassGroup,
    Session,
    SessionClass,
    SessionTask,
    Student,
    StudentSession,
    StudentTaskProgress,
    Submission,
    TaskCodeFragment,
    TaskTestCase,
    Teacher,
    TheoryQuizChoice,
    TheoryQuizMatchPair,
    TheoryQuizModule,
    TheoryQuizQuestion,
    TheoryMaterialBlock,
    TheoryMaterialModule,
    StudentTheoryQuizAttempt,

)
from .ui_translations import SUPPORTED_UI_LANGS, UI_TRANSLATIONS, get_ui_lang

PIN_RE = re.compile(r"^\d{6}$")
TEACHER_PIN_RE = re.compile(r"^\d{6}$")
SUBMIT_COOLDOWN_SECONDS = 15
SESSION_STATUS_DRAFT = "draft"
SESSION_STATUS_RUNNING = "running"
SESSION_STATUS_STOPPED = "closed"
SESSION_STATUSES = {
    SESSION_STATUS_DRAFT,
    SESSION_STATUS_RUNNING,
    SESSION_STATUS_STOPPED,
}

def _json_body(request: HttpRequest) -> dict:
    try:
        raw = request.body.decode("utf-8") if request.body else "{}"
        return json.loads(raw or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _normalize_session_status_in(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw == "stopped":
        return SESSION_STATUS_STOPPED
    return raw


def _normalize_session_status_out(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw == SESSION_STATUS_STOPPED:
        return "stopped"
    return raw


def _student_id(request: HttpRequest):
    return request.session.get("student_id") or None


def _teacher_id(request: HttpRequest):
    return request.session.get("teacher_id") or None


def _get_student_from_session(request: HttpRequest):
    student_id = request.session.get("student_id")
    class_id = request.session.get("student_class_id")
    if not student_id or not class_id:
        return None, None
    return student_id, class_id


def _get_logged_in_teacher(request: HttpRequest):
    teacher_id = request.session.get("teacher_id")
    if not teacher_id:
        return None
    return Teacher.objects.filter(id=teacher_id, is_active=True).first()


def _teacher_api_unauthorized():
    return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)


def teacher_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _teacher_id(request):
            return redirect("/teacher/login/")
        return view_func(request, *args, **kwargs)

    return _wrapped


def _parse_dt_or_none(value: str):
    s = (value or "").strip()
    if not s:
        return None

    dt = parse_datetime(s)
    if dt is None:
        return None

    if dj_tz.is_naive(dt):
        dt = dj_tz.make_aware(dt, dj_tz.get_current_timezone())
    return dt


def _get_active_session_for_class(class_id: int):
    session = (
        Session.objects.filter(status=SESSION_STATUS_RUNNING, allowed_classes__id=class_id)
        .distinct()
        .order_by("-starts_at", "-created_at")
        .first()
    )
    if not session or not session.is_active_now():
        return None
    return session


def _get_or_create_student_session(student_id: int, session: Session):
    now = timezone.now()
    ss, created = StudentSession.objects.get_or_create(
        student_id=student_id,
        session=session,
        defaults={"started_at": now, "last_seen_at": now},
    )
    if not created:
        StudentSession.objects.filter(id=ss.id).update(last_seen_at=now)
        ss.last_seen_at = now
    return ss, now


def _get_or_create_progress(ss: StudentSession, task: SessionTask, now=None):
    now = now or timezone.now()
    progress, created = StudentTaskProgress.objects.get_or_create(
        student_session=ss,
        task=task,
        defaults={"status": StudentTaskProgress.Status.IN_PROGRESS, "opened_at": now},
    )
    if not created:
        changed = []
        if not progress.opened_at:
            progress.opened_at = now
            changed.append("opened_at")
        if progress.status == StudentTaskProgress.Status.NOT_STARTED:
            progress.status = StudentTaskProgress.Status.IN_PROGRESS
            changed.append("status")
        if changed:
            progress.save(update_fields=changed)
    return progress


def _unlock_hints_if_needed(progress: StudentTaskProgress, now):
    changed = []

    if progress.attempts_failed >= 2 and not progress.hint1_unlocked_at:
        progress.hint1_unlocked_at = now
        changed.append("hint1_unlocked_at")

    if progress.attempts_failed >= 3 and not progress.hint2_unlocked_at:
        progress.hint2_unlocked_at = now
        changed.append("hint2_unlocked_at")

    if progress.attempts_failed >= 3 and not progress.hint3_unlocked_at:
        progress.hint3_unlocked_at = now
        changed.append("hint3_unlocked_at")

    return changed


def _get_task_fragments(task: SessionTask):
    frags = list(
        TaskCodeFragment.objects.filter(task=task, is_active=True)
        .order_by("position", "id")
        .values("position", "code")
    )

    top = ""
    bottom = ""
    for f in frags:
        code = (f.get("code") or "").rstrip()
        if not code:
            continue
        if f.get("position") == TaskCodeFragment.Position.TOP:
            top += code + "\n"
        else:
            bottom += code + "\n"

    return top.rstrip("\n"), bottom.rstrip("\n")


def _join_code(top_block: str, user_code: str, bottom_block: str) -> str:
    parts = []
    if top_block.strip():
        parts.append(top_block.rstrip() + "\n")
    parts.append((user_code or "").rstrip() + "\n")
    if bottom_block.strip():
        parts.append(bottom_block.rstrip() + "\n")
    return "".join(parts)


def _inc_hint_counter(progress: StudentTaskProgress, level: int) -> None:
    ActivityAggregate.objects.get_or_create(progress=progress)

    if level == 1:
        ActivityAggregate.objects.filter(progress=progress).update(
            hint1_requests=F("hint1_requests") + 1
        )
    elif level == 2:
        ActivityAggregate.objects.filter(progress=progress).update(
            hint2_requests=F("hint2_requests") + 1
        )
    elif level == 3:
        ActivityAggregate.objects.filter(progress=progress).update(
            hint3_requests=F("hint3_requests") + 1
        )

def _serialize_class_group(class_group: ClassGroup):
    students = list(
        Student.objects.filter(class_group=class_group, is_active=True)
        .order_by("full_name")
        .values("id", "full_name")
    )
    return {
        "id": class_group.id,
        "name": class_group.name,
        "student_count": len(students),
        "students": students,
    }


def _serialize_student(student: Student):
    return {
        "id": student.id,
        "full_name": student.full_name,
        "class": {
            "id": student.class_group_id,
            "name": student.class_group.name if student.class_group_id else "",
        },
        "is_active": student.is_active,
        "created_at": student.created_at.isoformat() if getattr(student, "created_at", None) else None,
    }


def _serialize_session(session: Session):
    class_links = list(
        SessionClass.objects.filter(session=session)
        .select_related("class_group")
        .order_by("class_group__name")
    )
    return {
        "id": session.id,
        "title": session.title,
        "description": session.description or "",
        "status": _normalize_session_status_out(session.status),
        "starts_at": session.starts_at.isoformat() if session.starts_at else None,
        "ends_at": session.ends_at.isoformat() if session.ends_at else None,
        "created_at": session.created_at.isoformat() if getattr(session, "created_at", None) else None,
        "class_group_ids": [x.class_group_id for x in class_links],
        "class_groups": [{"id": x.class_group_id, "name": x.class_group.name} for x in class_links],
        "tasks_count": SessionTask.objects.filter(session=session).count(),
        "student_sessions_count": StudentSession.objects.filter(session=session).count(),
        "is_active_now": session.is_active_now(),
    }


def _serialize_task(task: SessionTask):
    return {
        "id": task.id,
        "session_id": task.session_id,
        "position": task.position,
        "title": task.title,
        "statement": task.statement or "",
        "constraints": task.constraints or "",
        "programming_language": task.programming_language,
        "created_at": task.created_at.isoformat() if getattr(task, "created_at", None) else None,
    }

def _serialize_testcase(tc: TaskTestCase):
    return {
        "id": tc.id,
        "task_id": tc.task_id,
        "ordinal": tc.ordinal,
        "stdin": tc.stdin or "",
        "expected_stdout": tc.expected_stdout or "",
        "is_visible": tc.is_visible,
    }


def _serialize_fragment(frag: TaskCodeFragment):
    return {
        "id": frag.id,
        "task_id": frag.task_id,
        "position": frag.position,
        "title": frag.title or "",
        "code": frag.code or "",
        "is_active": frag.is_active,
        "created_at": frag.created_at.isoformat() if getattr(frag, "created_at", None) else None,
    }

def _serialize_theory_block(block: TheoryMaterialBlock):
    return {
        "id": block.id,
        "ordinal": block.ordinal,
        "block_type": block.block_type,
        "heading_level": block.heading_level or "",
        "content": block.content,
    }


def _serialize_theory_module(module: TheoryMaterialModule):
    return {
        "id": module.id,
        "session_id": module.session_id,
        "module_type": "theory_material",
        "position": module.position,
        "title": module.title,
        "topic": module.topic,
        "ai_prompt": module.ai_prompt,
        "is_active": module.is_active,
        "blocks": [_serialize_theory_block(b) for b in module.blocks.all().order_by("ordinal", "id")],
    }


def _serialize_theory_quiz_choice(choice: TheoryQuizChoice):
    return {
        "id": choice.id,
        "ordinal": choice.ordinal,
        "text": choice.text,
        "is_correct": choice.is_correct,
    }


def _serialize_theory_quiz_pair(pair: TheoryQuizMatchPair):
    return {
        "id": pair.id,
        "ordinal": pair.ordinal,
        "left_text": pair.left_text,
        "right_text": pair.right_text,
    }


def _serialize_theory_quiz_question(question: TheoryQuizQuestion):
    return {
        "id": question.id,
        "ordinal": question.ordinal,
        "question_type": question.question_type,
        "prompt": question.prompt,
        "model_answer": question.model_answer,
        "accept_suitable_answer": question.accept_suitable_answer,
        "choices": [_serialize_theory_quiz_choice(x) for x in question.choices.all().order_by("ordinal", "id")],
        "pairs": [_serialize_theory_quiz_pair(x) for x in question.pairs.all().order_by("ordinal", "id")],
    }


def _serialize_theory_quiz_module(module: TheoryQuizModule):
    return {
        "id": module.id,
        "session_id": module.session_id,
        "module_type": "theory_quiz",
        "position": module.position,
        "title": module.title,
        "topic": module.topic,
        "instructions": module.instructions,
        "is_active": module.is_active,
        "questions": [
            _serialize_theory_quiz_question(q)
            for q in module.questions.all().order_by("ordinal", "id")
        ],
    }


def _is_module_position_taken(session: Session, position: int, *, skip_type: str = "", skip_id: int | None = None) -> bool:
    if SessionTask.objects.filter(session=session, position=position).exclude(
        id=skip_id if skip_type == "coding_task" else None
    ).exists():
        return True

    if TheoryMaterialModule.objects.filter(session=session, position=position).exclude(
        id=skip_id if skip_type == "theory_material" else None
    ).exists():
        return True

    if TheoryQuizModule.objects.filter(session=session, position=position).exclude(
        id=skip_id if skip_type == "theory_quiz" else None
    ).exists():
        return True

    return False


def _normalize_open_answer_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _get_persisted_dashboard_class_id(request: HttpRequest) -> str:
    class_id = (request.GET.get("class_id") or "").strip()
    if class_id:
        request.session["dashboard_class_id"] = class_id
        request.session.modified = True
        return class_id
    return str(request.session.get("dashboard_class_id") or "")


def _parse_theory_quiz_question_payload(data: dict):
    question_type = (data.get("question_type") or "").strip()
    prompt = (data.get("prompt") or "").strip()
    model_answer = (data.get("model_answer") or "").strip()
    accept_suitable_answer = bool(data.get("accept_suitable_answer"))
    choices = data.get("choices") or []
    pairs = data.get("pairs") or []

    if question_type not in {
        TheoryQuizQuestion.QuestionType.SINGLE_CHOICE,
        TheoryQuizQuestion.QuestionType.OPEN_ANSWER,
        TheoryQuizQuestion.QuestionType.MATCHING,
    }:
        return None, JsonResponse({"ok": False, "error": "invalid question_type"}, status=400)

    if not prompt:
        return None, JsonResponse({"ok": False, "error": "prompt is required"}, status=400)

    parsed = {
        "question_type": question_type,
        "prompt": prompt,
        "model_answer": model_answer,
        "accept_suitable_answer": accept_suitable_answer,
        "choices": [],
        "pairs": [],
    }

    if question_type == TheoryQuizQuestion.QuestionType.SINGLE_CHOICE:
        if not isinstance(choices, list) or len(choices) < 2:
            return None, JsonResponse({"ok": False, "error": "single_choice requires at least 2 choices"}, status=400)

        correct_found = False
        for idx, item in enumerate(choices, start=1):
            text = (item.get("text") or "").strip() if isinstance(item, dict) else ""
            is_correct = bool(item.get("is_correct")) if isinstance(item, dict) else False
            if not text:
                return None, JsonResponse({"ok": False, "error": "each choice must have text"}, status=400)
            if is_correct:
                correct_found = True
            parsed["choices"].append({"ordinal": idx, "text": text, "is_correct": is_correct})

        if not correct_found:
            return None, JsonResponse({"ok": False, "error": "single_choice requires one correct choice"}, status=400)

    elif question_type == TheoryQuizQuestion.QuestionType.OPEN_ANSWER:
        if not model_answer:
            return None, JsonResponse({"ok": False, "error": "open_answer requires model_answer"}, status=400)

    elif question_type == TheoryQuizQuestion.QuestionType.MATCHING:
        if not isinstance(pairs, list) or len(pairs) < 2:
            return None, JsonResponse({"ok": False, "error": "matching requires at least 2 pairs"}, status=400)

        for idx, item in enumerate(pairs, start=1):
            if not isinstance(item, dict):
                return None, JsonResponse({"ok": False, "error": "invalid matching pair payload"}, status=400)
            left_text = (item.get("left_text") or "").strip()
            right_text = (item.get("right_text") or "").strip()
            if not left_text or not right_text:
                return None, JsonResponse({"ok": False, "error": "matching pairs require left_text and right_text"}, status=400)
            parsed["pairs"].append({"ordinal": idx, "left_text": left_text, "right_text": right_text})

    return parsed, None


# -------------------------
# Student auth API
# -------------------------

@csrf_exempt
@require_POST
def student_login(request: HttpRequest):
    data = _json_body(request)
    full_name = (data.get("full_name") or "").strip()
    pin = str(data.get("pin") or "").strip()

    if not full_name or not pin:
        return JsonResponse({"ok": False, "error": "full_name and pin are required"}, status=400)
    if not PIN_RE.match(pin):
        return JsonResponse({"ok": False, "error": "pin must be 6 digits"}, status=400)

    student = (
        Student.objects.select_related("class_group")
        .filter(full_name__iexact=full_name, is_active=True)
        .first()
    )
    if not student:
        return JsonResponse({"ok": False, "error": "student not found"}, status=404)
    if not student.check_pin(pin):
        return JsonResponse({"ok": False, "error": "invalid credentials"}, status=401)

    request.session["student_id"] = student.id
    request.session["student_name"] = student.full_name
    request.session["student_class_id"] = student.class_group_id
    request.session["student_logged_in_at"] = timezone.now().isoformat()

    return JsonResponse({
        "ok": True,
        "student": {
            "id": student.id,
            "full_name": student.full_name,
            "class": {"id": student.class_group_id, "name": student.class_group.name},
        },
    })


@csrf_exempt
@require_POST
def student_logout(request: HttpRequest):
    request.session.flush()
    return JsonResponse({"ok": True})


@require_GET
def student_me(request: HttpRequest):
    student_id = _student_id(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    student = (
        Student.objects.select_related("class_group")
        .filter(id=student_id, is_active=True)
        .first()
    )
    if not student:
        request.session.flush()
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    return JsonResponse({
        "ok": True,
        "student": {
            "id": student.id,
            "full_name": student.full_name,
            "class": {"id": student.class_group_id, "name": student.class_group.name},
        },
    })


# -------------------------
# Student session/task API
# -------------------------

@require_GET
def student_active_session(request: HttpRequest):
    student_id = _student_id(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    class_id = request.session.get("student_class_id")
    if not class_id:
        return JsonResponse({"ok": False, "error": "invalid session"}, status=401)

    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse(
            {"ok": True, "active": False, "message": "Current session is inactive"},
            status=200,
        )

    ss, _ = _get_or_create_student_session(student_id, session)

    coding_tasks = list(
        SessionTask.objects.filter(session=session)
        .order_by("position", "id")
        .values("id", "position", "title")
    )
    theory_modules = list(
        TheoryMaterialModule.objects.filter(session=session, is_active=True)
        .order_by("position", "id")
        .values("id", "position", "title")
    )
    theory_quizzes = list(
        TheoryQuizModule.objects.filter(session=session, is_active=True)
        .order_by("position", "id")
        .values("id", "position", "title")
    )

    progress_qs = StudentTaskProgress.objects.filter(student_session=ss).values(
        "task_id", "status", "attempts_total", "attempts_failed"
    )
    progress_map = {p["task_id"]: p for p in progress_qs}

    tasks_out = []

    for t in coding_tasks:
        p = progress_map.get(t["id"])
        tasks_out.append(
            {
                "id": t["id"],
                "position": t["position"],
                "title": t["title"],
                "module_type": "coding_task",
                "progress": p or {
                    "status": "not_started",
                    "attempts_total": 0,
                    "attempts_failed": 0,
                },
            }
        )

    for m in theory_modules:
        tasks_out.append(
            {
                "id": m["id"],
                "position": m["position"],
                "title": m["title"],
                "module_type": "theory_material",
                "progress": {
                    "status": "not_started",
                    "attempts_total": 0,
                    "attempts_failed": 0,
                },
            }
        )

    for q in theory_quizzes:
        tasks_out.append(
            {
                "id": q["id"],
                "position": q["position"],
                "title": q["title"],
                "module_type": "theory_quiz",
                "progress": {
                    "status": "not_started",
                    "attempts_total": 0,
                    "attempts_failed": 0,
                },
            }
        )

    tasks_out.sort(key=lambda x: (x["position"], x["module_type"], x["id"]))

    return JsonResponse(
        {
            "ok": True,
            "active": True,
            "session": {
                "id": session.id,
                "title": session.title,
                "description": session.description,
                "starts_at": session.starts_at.isoformat() if session.starts_at else None,
                "ends_at": session.ends_at.isoformat() if session.ends_at else None,
            },
            "tasks": tasks_out,
        }
    )



@require_GET
def student_task_detail(request: HttpRequest, task_id: int):
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse({"ok": True, "active": False, "message": "Current session is inactive"}, status=200)

    task = get_object_or_404(SessionTask, id=task_id, session=session)
    ss, now = _get_or_create_student_session(student_id, session)
    progress = _get_or_create_progress(ss, task, now)

    if progress.status == StudentTaskProgress.Status.SOLVED and progress.locked_after_solve:
        return JsonResponse(
            {"ok": True, "locked": True, "message": "Task already solved and locked"},
            status=200,
        )

    visible_tests = list(
        TaskTestCase.objects.filter(task=task, is_visible=True)
        .order_by("ordinal")
        .values("ordinal", "stdin", "expected_stdout")
    )
    top_frag, bottom_frag = _get_task_fragments(task)

    return JsonResponse(
        {
            "ok": True,
            "locked": False,
            "task": {
                "id": task.id,
                "position": task.position,
                "title": task.title,
                "statement": task.statement,
                "constraints": task.constraints,
                "programming_language": task.programming_language,
            },
            "progress": {
                "status": progress.status,
                "attempts_total": progress.attempts_total,
                "attempts_failed": progress.attempts_failed,
                "hint1_available": bool(progress.hint1_unlocked_at),
                "hint2_available": bool(progress.hint2_unlocked_at),
                "hint3_available": bool(progress.hint3_unlocked_at),
            },
            "visible_testcases": visible_tests,
            "code_fragments": {
                "top": top_frag,
                "bottom": bottom_frag,
            },
        }
    )
@require_GET
def student_theory_module_detail(request: HttpRequest, module_id: int):
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse({"ok": True, "active": False, "message": "Current session is inactive"}, status=200)

    module = get_object_or_404(
        TheoryMaterialModule.objects.prefetch_related("blocks"),
        id=module_id,
        session=session,
        is_active=True,
    )

    return JsonResponse({
        "ok": True,
        "module": {
            "id": module.id,
            "position": module.position,
            "title": module.title,
            "topic": module.topic,
            "module_type": "theory_material",
            "blocks": [_serialize_theory_block(b) for b in module.blocks.all().order_by("ordinal", "id")],
        },
    })


@require_GET
def student_theory_quiz_detail(request: HttpRequest, module_id: int):
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse({"ok": True, "active": False, "message": "Current session is inactive"}, status=200)

    module = get_object_or_404(
        TheoryQuizModule.objects.prefetch_related("questions__choices", "questions__pairs"),
        id=module_id,
        session=session,
        is_active=True,
    )

    ss, _ = _get_or_create_student_session(student_id, session)
    last_attempt = (
        StudentTheoryQuizAttempt.objects.filter(student_session=ss, module=module)
        .order_by("-attempt_no")
        .first()
    )

    questions_out = []
    for question in module.questions.all().order_by("ordinal", "id"):
        row = {
            "id": question.id,
            "ordinal": question.ordinal,
            "question_type": question.question_type,
            "prompt": question.prompt,
        }

        if question.question_type == TheoryQuizQuestion.QuestionType.SINGLE_CHOICE:
            row["choices"] = [
                {
                    "id": choice.id,
                    "ordinal": choice.ordinal,
                    "text": choice.text,
                }
                for choice in question.choices.all().order_by("ordinal", "id")
            ]
        elif question.question_type == TheoryQuizQuestion.QuestionType.MATCHING:
            left_items = []
            right_items = []
            for pair in question.pairs.all().order_by("ordinal", "id"):
                left_items.append({"id": pair.id, "text": pair.left_text})
                right_items.append({"id": pair.id, "text": pair.right_text})
            random.shuffle(right_items)
            row["left_items"] = left_items
            row["right_items"] = right_items

        questions_out.append(row)

    return JsonResponse({
        "ok": True,
        "module": {
            "id": module.id,
            "position": module.position,
            "title": module.title,
            "topic": module.topic,
            "instructions": module.instructions,
            "module_type": "theory_quiz",
            "questions": questions_out,
        },
        "last_attempt": {
            "attempt_no": last_attempt.attempt_no,
            "score_percent": float(last_attempt.score_percent),
            "correct_answers": last_attempt.correct_answers,
            "total_questions": last_attempt.total_questions,
            "result_json": last_attempt.result_json,
        } if last_attempt else None,
    })


@csrf_exempt
@require_POST
def student_theory_quiz_submit(request: HttpRequest, module_id: int):
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse({"ok": True, "active": False, "message": "Current session is inactive"}, status=200)

    module = get_object_or_404(
        TheoryQuizModule.objects.prefetch_related("questions__choices", "questions__pairs"),
        id=module_id,
        session=session,
        is_active=True,
    )

    data = _json_body(request)
    answers = data.get("answers") or {}
    if not isinstance(answers, dict):
        return JsonResponse({"ok": False, "error": "answers must be an object"}, status=400)

    ss, _ = _get_or_create_student_session(student_id, session)
    next_attempt_no = (
        StudentTheoryQuizAttempt.objects.filter(student_session=ss, module=module).count() + 1
    )

    results = []
    correct_answers = 0
    total_questions = 0

    for question in module.questions.all().order_by("ordinal", "id"):
        total_questions += 1
        answer_key = str(question.id)
        student_value = answers.get(answer_key)
        is_correct = False
        feedback = ""

        if question.question_type == TheoryQuizQuestion.QuestionType.SINGLE_CHOICE:
            try:
                selected_id = int(student_value)
            except (TypeError, ValueError):
                selected_id = None
            correct_choice = question.choices.filter(is_correct=True).order_by("ordinal", "id").first()
            is_correct = bool(correct_choice and selected_id == correct_choice.id)
            feedback = "" if is_correct else "Incorrect choice."

        elif question.question_type == TheoryQuizQuestion.QuestionType.MATCHING:
            submitted_map = student_value if isinstance(student_value, dict) else {}
            is_correct = True
            for pair in question.pairs.all().order_by("ordinal", "id"):
                selected_right = submitted_map.get(str(pair.id))
                try:
                    selected_right = int(selected_right)
                except (TypeError, ValueError):
                    selected_right = None
                if selected_right != pair.id:
                    is_correct = False
                    break
            feedback = "" if is_correct else "Some matches are incorrect."

        elif question.question_type == TheoryQuizQuestion.QuestionType.OPEN_ANSWER:
            student_answer = (student_value or "").strip()
            model_answer = (question.model_answer or "").strip()
            if student_answer and _normalize_open_answer_text(student_answer) == _normalize_open_answer_text(model_answer):
                is_correct = True
                feedback = ""
            elif student_answer and model_answer:
                try:
                    prompt_snapshot = build_theory_open_answer_prompt_snapshot(
                        session_title=session.title,
                        session_description=session.description,
                        module_title=module.title,
                        question_prompt=question.prompt,
                        model_answer=model_answer,
                        student_answer=student_answer,
                        accept_suitable_answer=question.accept_suitable_answer,
                    )
                    ai_result = call_openai_theory_open_answer(prompt_snapshot).get("data") or {}
                    is_correct = bool(ai_result.get("is_correct"))
                    feedback = (ai_result.get("feedback") or "").strip()
                except Exception:
                    is_correct = False
                    feedback = "Answer could not be verified automatically."
            else:
                is_correct = False
                feedback = "Answer is required."

        if is_correct:
            correct_answers += 1

        results.append({
            "question_id": question.id,
            "ordinal": question.ordinal,
            "question_type": question.question_type,
            "is_correct": is_correct,
            "feedback": feedback,
        })

    score_percent = round((correct_answers * 100.0 / total_questions), 2) if total_questions > 0 else 0.0

    attempt = StudentTheoryQuizAttempt.objects.create(
        student_session=ss,
        module=module,
        attempt_no=next_attempt_no,
        score_percent=score_percent,
        correct_answers=correct_answers,
        total_questions=total_questions,
        answers_json=answers,
        result_json={"results": results},
    )

    return JsonResponse({
        "ok": True,
        "attempt": {
            "id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "score_percent": float(attempt.score_percent),
            "correct_answers": attempt.correct_answers,
            "total_questions": attempt.total_questions,
            "results": results,
        },
    })



@csrf_exempt
@require_POST
def student_submit(request: HttpRequest, task_id: int):
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse({"ok": True, "active": False, "message": "Current session is inactive"}, status=200)

    task = get_object_or_404(SessionTask, id=task_id, session=session)
    data = _json_body(request)
    user_code = (data.get("code") or "").rstrip()

    if not user_code:
        return JsonResponse({"ok": False, "error": "code is required"}, status=400)

    ss, now = _get_or_create_student_session(student_id, session)
    progress = _get_or_create_progress(ss, task, now)

    if progress.status == StudentTaskProgress.Status.SOLVED and progress.locked_after_solve:
        return JsonResponse({"ok": True, "locked": True, "message": "Task already solved"}, status=200)

    if progress.last_submit_at:
        delta = (now - progress.last_submit_at).total_seconds()
        if delta < SUBMIT_COOLDOWN_SECONDS:
            wait_seconds = max(1, int(SUBMIT_COOLDOWN_SECONDS - delta))
            return JsonResponse(
                {"ok": False, "error": f"Too frequent submits. Wait {wait_seconds}s"},
                status=429,
            )

    code_hash = hashlib.sha256(user_code.encode("utf-8")).hexdigest()
    if progress.last_code_hash and progress.last_code_hash == code_hash:
        return JsonResponse({"ok": False, "error": "No changes in code since last submit"}, status=400)

    testcases = list(
        TaskTestCase.objects.filter(task=task)
        .order_by("ordinal")
        .values("ordinal", "stdin", "expected_stdout")
    )
    if not testcases:
        return JsonResponse({"ok": False, "error": "No testcases configured for this task"}, status=500)

    progress.attempts_total += 1
    attempt_no = progress.attempts_total

    top_frag, bottom_frag = _get_task_fragments(task)
    final_code = _join_code(top_frag, user_code, bottom_frag)

    try:
        tokens = create_batch_submissions(
            final_code,
            testcases,
            programming_language=task.programming_language,
        )
        results = wait_batch(tokens, timeout_sec=30, poll_interval=0.9)
    except Exception as e:
        progress.last_submit_at = now
        progress.last_code_hash = code_hash
        progress.attempts_failed += 1

        changed = [
            "attempts_total",
            "attempts_failed",
            "last_submit_at",
            "last_code_hash",
        ]
        changed += _unlock_hints_if_needed(progress, now)
        progress.save(update_fields=changed)

        sub = Submission.objects.create(
            progress=progress,
            attempt_no=attempt_no,
            code=user_code,
            verdict=Submission.Verdict.RUNTIME_ERROR,
            stdout="",
            stderr=f"Judge0 error: {type(e).__name__}: {e}",
            passed_tests=0,
            total_tests=len(testcases),
        )

        return JsonResponse(
            {
                "ok": True,
                "submission": {
                    "id": sub.id,
                    "attempt_no": sub.attempt_no,
                    "verdict": sub.verdict,
                    "stdout": sub.stdout,
                    "stderr": sub.stderr,
                    "passed_tests": sub.passed_tests,
                    "total_tests": sub.total_tests,
                },
                "progress": {
                    "status": progress.status,
                    "attempts_total": progress.attempts_total,
                    "attempts_failed": progress.attempts_failed,
                    "hint1_available": bool(progress.hint1_unlocked_at),
                    "hint2_available": bool(progress.hint2_unlocked_at),
                    "hint3_available": bool(progress.hint3_unlocked_at),
                },
            }
        )

    passed = 0
    stdout_last = ""
    stderr_last = ""
    verdict = Submission.Verdict.WRONG_ANSWER

    for r in results:
        stdout_last = getattr(r, "stdout", "") or ""
        stderr_last = (
            getattr(r, "stderr", "")
            or getattr(r, "compile_output", "")
            or getattr(r, "message", "")
            or ""
        )
        status_id = getattr(r, "status_id", None)

        if status_id == 3:
            passed += 1
            continue
        if status_id == 4:
            verdict = Submission.Verdict.WRONG_ANSWER
            break
        if status_id == 5:
            verdict = Submission.Verdict.TIME_LIMIT
            break
        if status_id == 6:
            verdict = Submission.Verdict.COMPILATION_ERROR
            break
        if status_id and status_id >= 7:
            verdict = Submission.Verdict.RUNTIME_ERROR
            break

        verdict = Submission.Verdict.RUNTIME_ERROR
        break

    if passed == len(results):
        verdict = Submission.Verdict.ACCEPTED

    progress.last_submit_at = now
    progress.last_code_hash = code_hash
    changed = ["attempts_total", "last_submit_at", "last_code_hash"]

    if verdict == Submission.Verdict.ACCEPTED:
        progress.status = StudentTaskProgress.Status.SOLVED
        progress.solved_at = now
        progress.locked_after_solve = True
        changed += ["status", "solved_at", "locked_after_solve"]
    else:
        progress.attempts_failed += 1
        changed.append("attempts_failed")
        changed += _unlock_hints_if_needed(progress, now)

    progress.save(update_fields=changed)

    sub = Submission.objects.create(
        progress=progress,
        attempt_no=attempt_no,
        code=user_code,
        verdict=verdict,
        stdout=stdout_last,
        stderr=stderr_last,
        passed_tests=passed,
        total_tests=len(results),
    )

    return JsonResponse(
        {
            "ok": True,
            "submission": {
                "id": sub.id,
                "attempt_no": sub.attempt_no,
                "verdict": sub.verdict,
                "stdout": sub.stdout,
                "stderr": sub.stderr,
                "passed_tests": sub.passed_tests,
                "total_tests": sub.total_tests,
            },
            "progress": {
                "status": progress.status,
                "attempts_total": progress.attempts_total,
                "attempts_failed": progress.attempts_failed,
                "hint1_available": bool(progress.hint1_unlocked_at),
                "hint2_available": bool(progress.hint2_unlocked_at),
                "hint3_available": bool(progress.hint3_unlocked_at),
            },
        }
    )



from .ai_assist import (
    build_prompt_snapshot,
    call_openai_hint,
    sanitize_no_code,
    build_solution_prompt_snapshot,
    call_openai_solution,
    build_theory_open_answer_prompt_snapshot,
    build_theory_material_prompt_snapshot,
    call_openai_theory_open_answer,
    call_openai_theory_material,
)

@require_GET
def student_hint_level(request: HttpRequest, task_id: int, level: int):
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    if level not in (1, 2, 3):
        return JsonResponse({"ok": False, "error": "invalid level"}, status=400)

    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse({"ok": True, "active": False, "message": "Current session is inactive"}, status=200)

    task = get_object_or_404(SessionTask, id=task_id, session=session)

    now = timezone.now()
    ss, _ = StudentSession.objects.get_or_create(
        student_id=student_id,
        session=session,
        defaults={"started_at": now, "last_seen_at": now},
    )
    StudentSession.objects.filter(id=ss.id).update(last_seen_at=now)

    progress, _ = StudentTaskProgress.objects.get_or_create(
        student_session=ss,
        task=task,
        defaults={"status": StudentTaskProgress.Status.IN_PROGRESS, "opened_at": now},
    )

    if level == 1 and progress.attempts_failed < 2:
        return JsonResponse({"ok": False, "error": "hint level 1 not available yet"}, status=403)
    if level == 2 and progress.attempts_failed < 3:
        return JsonResponse({"ok": False, "error": "hint level 2 not available yet"}, status=403)
    if level == 3 and progress.attempts_failed < 3:
        return JsonResponse({"ok": False, "error": "hint level 3 not available yet"}, status=403)

    if level == 1 and progress.hint1_text:
        _inc_hint_counter(progress, 1)
        return JsonResponse({"ok": True, "level": 1, "kind": "text", "text": progress.hint1_text})

    if level == 2 and progress.hint2_text:
        _inc_hint_counter(progress, 2)
        return JsonResponse({"ok": True, "level": 2, "kind": "text", "text": progress.hint2_text})

    if level == 3 and progress.hint3_text:
        progress.hint3_used_at = now
        progress.save(update_fields=["hint3_used_at"])
        _inc_hint_counter(progress, 3)
        return JsonResponse(
            {
                "ok": True,
                "level": 3,
                "kind": "code",
                "code": progress.hint3_text,
                "insert_into_editor": True,
            }
        )

    cached = (
        AiAssistMessage.objects
        .filter(progress=progress, level=level, status=AiAssistMessage.Status.OK)
        .order_by("-created_at")
        .first()
    )

    if cached and cached.response_text:
        if level == 1:
            progress.hint1_text = cached.response_text
            progress.save(update_fields=["hint1_text"])
            _inc_hint_counter(progress, 1)
            return JsonResponse({"ok": True, "level": 1, "kind": "text", "text": cached.response_text})

        if level == 2:
            progress.hint2_text = cached.response_text
            progress.save(update_fields=["hint2_text"])
            _inc_hint_counter(progress, 2)
            return JsonResponse({"ok": True, "level": 2, "kind": "text", "text": cached.response_text})

        if level == 3:
            progress.hint3_text = cached.response_text
            progress.hint3_used_at = now
            progress.save(update_fields=["hint3_text", "hint3_used_at"])
            _inc_hint_counter(progress, 3)
            return JsonResponse(
                {
                    "ok": True,
                    "level": 3,
                    "kind": "code",
                    "code": cached.response_text,
                    "insert_into_editor": True,
                }
            )

    visible_tests = list(
        TaskTestCase.objects.filter(task=task, is_visible=True)
        .order_by("ordinal")
        .values("stdin", "expected_stdout")
    )
    last_sub = Submission.objects.filter(progress=progress).order_by("-attempt_no").first()
    last_subs = list(Submission.objects.filter(progress=progress).order_by("attempt_no")[:50])

    ui_lang = get_ui_lang(request)

    if level in (1, 2):
        prompt_snapshot = build_prompt_snapshot(
            level=level,
            statement=task.statement,
            constraints=task.constraints,
            visible_tests=visible_tests,
            last_submission=last_sub,
            last_submissions=last_subs,
            programming_language=task.programming_language,
            interface_language=ui_lang,
        )
    else:
        top_frag, bottom_frag = _get_task_fragments(task)
        prompt_snapshot = build_solution_prompt_snapshot(
            session_title=session.title,
            session_description=session.description,
            statement=task.statement,
            constraints=task.constraints,
            visible_tests=visible_tests,
            last_submission=last_sub,
            last_submissions=last_subs,
            top_fragment=top_frag,
            bottom_fragment=bottom_frag,
            programming_language=task.programming_language,
            interface_language=ui_lang,
        )


    msg = AiAssistMessage.objects.create(
        progress=progress,
        level=level,
        prompt_snapshot=prompt_snapshot,
        status=AiAssistMessage.Status.ERROR,
        error_message="pending",
    )

    try:
        if level in (1, 2):
            out = call_openai_hint(level, prompt_snapshot)
            if out is None or not isinstance(out, dict):
                raise RuntimeError("call_openai_hint returned invalid response")

            data = out.get("data")
            if data is None or not isinstance(data, dict):
                raise RuntimeError("OpenAI response missing 'data'")

            text = (data.get("text") or "").strip()
            if not text:
                raise RuntimeError("Empty AI response text")

            text = sanitize_no_code(text)

            msg.response_text = text
            msg.model = out.get("model", "")
            msg.tokens_in = out.get("tokens_in")
            msg.tokens_out = out.get("tokens_out")
            msg.status = AiAssistMessage.Status.OK
            msg.error_message = ""
            msg.save(update_fields=["response_text", "model", "tokens_in", "tokens_out", "status", "error_message"])

            if level == 1:
                progress.hint1_text = text
                progress.save(update_fields=["hint1_text"])
            else:
                progress.hint2_text = text
                progress.save(update_fields=["hint2_text"])

            _inc_hint_counter(progress, level)
            return JsonResponse({"ok": True, "level": level, "kind": "text", "text": text})

        out = call_openai_solution(prompt_snapshot)
        if out is None or not isinstance(out, dict):
            raise RuntimeError("call_openai_solution returned invalid response")

        data = out.get("data")
        if data is None or not isinstance(data, dict):
            raise RuntimeError("OpenAI response missing 'data'")

        code = (data.get("code") or "").strip()
        if not code:
            raise RuntimeError("Empty solution code")

        msg.response_text = code
        msg.model = out.get("model", "")
        msg.tokens_in = out.get("tokens_in")
        msg.tokens_out = out.get("tokens_out")
        msg.status = AiAssistMessage.Status.OK
        msg.error_message = ""
        msg.save(update_fields=["response_text", "model", "tokens_in", "tokens_out", "status", "error_message"])

        progress.hint3_text = code
        progress.hint3_used_at = now
        progress.save(update_fields=["hint3_text", "hint3_used_at"])

        _inc_hint_counter(progress, 3)

        return JsonResponse(
            {
                "ok": True,
                "level": 3,
                "kind": "code",
                "code": code,
                "insert_into_editor": True,
            }
        )

    except Exception as e:
        msg.status = AiAssistMessage.Status.ERROR
        msg.error_message = f"{type(e).__name__}: {e}"
        msg.save(update_fields=["status", "error_message"])
        return JsonResponse({"ok": False, "error": "AI assistant temporarily unavailable"}, status=502)


# -------------------------
# Student pages
# -------------------------

@require_http_methods(["GET", "POST"])
def student_login_page(request: HttpRequest):
    lang = get_ui_lang(request)
    T = UI_TRANSLATIONS.get(lang, UI_TRANSLATIONS["en"])

    if request.method == "GET":
        return render(request, "core/student_login.html")

    full_name = (request.POST.get("full_name") or "").strip()
    pin = (request.POST.get("pin") or "").strip()

    if not full_name or not pin or not PIN_RE.match(pin):
        return render(
            request,
            "core/student_login.html",
            {"error": T.get("student_login_error_required", "Enter name and PIN (6 digits).")},
        )

    student = (
        Student.objects.select_related("class_group")
        .filter(full_name__iexact=full_name, is_active=True)
        .first()
    )
    if not student or not student.check_pin(pin):
        return render(
            request,
            "core/student_login.html",
            {"error": T.get("student_login_error_invalid", "Invalid name or PIN.")},
        )

    request.session["student_id"] = student.id
    request.session["student_name"] = student.full_name
    request.session["student_class_id"] = student.class_group_id
    request.session["student_logged_in_at"] = timezone.now().isoformat()
    return redirect("/student/")


def student_portal_page(request: HttpRequest):
    if not _student_id(request):
        return redirect("/student/login/")
    return render(request, "core/student_portal.html")


@require_POST
def student_logout_page(request: HttpRequest):
    request.session.flush()
    return redirect("/student/login/")


# -------------------------
# Admin analytics pages
# -------------------------

def _scale_totals_if_needed(values, other_max):
    if not values:
        return values, 1
    mx = max(values) or 0
    if other_max <= 0:
        other_max = 1
    ratio = mx / other_max if other_max else mx
    if ratio >= 10:
        return [v / 10 for v in values], 10
    return values, 1


@staff_member_required
@staff_member_required
def admin_stats_dashboard(request: HttpRequest) -> HttpResponse:
    context = _build_dashboard_analytics_context(request)
    return render(request, "core/admin_stats_dashboard.html", context)


@staff_member_required
@staff_member_required
def admin_student_profile(request: HttpRequest, student_id: int) -> HttpResponse:
    student = get_object_or_404(
        Student.objects.select_related("class_group"),
        id=student_id,
        is_active=True,
    )

    ss_qs = (
        StudentSession.objects.filter(student=student)
        .select_related("session")
        .order_by("session__starts_at", "session__created_at", "id")
    )

    student_session_ids = list(ss_qs.values_list("id", flat=True))
    session_ids = list(ss_qs.values_list("session_id", flat=True))

    total_tasks_map = {
        row["session_id"]: row["c"]
        for row in SessionTask.objects.filter(session_id__in=session_ids)
        .values("session_id")
        .annotate(c=Count("id"))
    }

    solved_progress_map = {
        row["student_session_id"]: row["c"]
        for row in StudentTaskProgress.objects.filter(student_session_id__in=student_session_ids)
        .filter(
            Q(status=StudentTaskProgress.Status.SOLVED)
            | Q(status=StudentTaskProgress.Status.LOCKED)
            | Q(solved_at__isnull=False)
        )
        .values("student_session_id")
        .annotate(c=Count("task_id", distinct=True))
    }

    submission_map = {
        row["progress__student_session_id"]: {
            "total": row["total"],
            "accepted": row["accepted"],
        }
        for row in Submission.objects.filter(progress__student_session_id__in=student_session_ids)
        .values("progress__student_session_id")
        .annotate(
            total=Count("id"),
            accepted=Count("id", filter=Q(verdict=Submission.Verdict.ACCEPTED)),
        )
    }

    labels = []
    task_completion_rates = []
    success_rates = []
    solved_counts = []
    total_tasks = []
    accepted_counts = []
    total_attempts = []

    for ss in ss_qs:
        session = ss.session
        label = session.title or f"Session {session.id}"

        session_total_tasks = int(total_tasks_map.get(session.id, 0) or 0)
        session_solved = int(solved_progress_map.get(ss.id, 0) or 0)

        sub_data = submission_map.get(ss.id, {"total": 0, "accepted": 0})
        session_total_attempts = int(sub_data["total"] or 0)
        session_accepted = int(sub_data["accepted"] or 0)

        task_completion_pct = round((session_solved * 100.0 / session_total_tasks), 2) if session_total_tasks > 0 else 0.0
        success_pct = round((session_accepted * 100.0 / session_total_attempts), 2) if session_total_attempts > 0 else 0.0

        labels.append(label)
        task_completion_rates.append(task_completion_pct)
        success_rates.append(success_pct)
        solved_counts.append(session_solved)
        total_tasks.append(session_total_tasks)
        accepted_counts.append(session_accepted)
        total_attempts.append(session_total_attempts)

    chart = {
        "labels": labels,
        "task_completion_rates": task_completion_rates,
        "success_rates": success_rates,
        "solved_counts": solved_counts,
        "total_tasks": total_tasks,
        "accepted_counts": accepted_counts,
        "total_attempts": total_attempts,
    }

    return render(
        request,
        "core/admin_student_profile.html",
        {
            "student": student,
            "chart_json": json.dumps(chart, ensure_ascii=False),
            "active": "",
        },
    )


# -------------------------
# Teacher auth/pages
# -------------------------

@csrf_exempt
@require_POST
def teacher_login(request: HttpRequest):
    data = _json_body(request)
    full_name = (data.get("full_name") or "").strip()
    pin = str(data.get("pin") or "").strip()

    if not full_name or not pin:
        return JsonResponse({"ok": False, "error": "full_name and pin are required"}, status=400)
    if not TEACHER_PIN_RE.match(pin):
        return JsonResponse({"ok": False, "error": "pin must be 6 digits"}, status=400)

    teacher = Teacher.objects.filter(full_name__iexact=full_name, is_active=True).first()
    if not teacher:
        return JsonResponse({"ok": False, "error": "teacher not found"}, status=404)
    if not teacher.check_pin(pin):
        return JsonResponse({"ok": False, "error": "invalid credentials"}, status=401)

    request.session["teacher_id"] = teacher.id
    request.session["teacher_name"] = teacher.full_name
    request.session["teacher_logged_in_at"] = timezone.now().isoformat()

    return JsonResponse({"ok": True, "teacher": {"id": teacher.id, "full_name": teacher.full_name}})


@csrf_exempt
@require_POST
def teacher_logout(request: HttpRequest):
    for key in ["teacher_id", "teacher_name", "teacher_logged_in_at"]:
        request.session.pop(key, None)
    return JsonResponse({"ok": True})


@require_GET
def teacher_me(request: HttpRequest):
    teacher_id = _teacher_id(request)
    if not teacher_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    teacher = Teacher.objects.filter(id=teacher_id, is_active=True).first()
    if not teacher:
        for key in ["teacher_id", "teacher_name", "teacher_logged_in_at"]:
            request.session.pop(key, None)
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    return JsonResponse({"ok": True, "teacher": {"id": teacher.id, "full_name": teacher.full_name}})


@require_http_methods(["GET", "POST"])
def teacher_login_page(request: HttpRequest):
    if request.method == "GET":
        return render(request, "core/teacher_login.html")

    full_name = (request.POST.get("full_name") or "").strip()
    pin = (request.POST.get("pin") or "").strip()

    if not full_name or not pin or not TEACHER_PIN_RE.match(pin):
        return render(request, "core/teacher_login.html", {"error": "Enter name and PIN (6 digits)."})

    teacher = Teacher.objects.filter(full_name__iexact=full_name, is_active=True).first()
    if not teacher or not teacher.check_pin(pin):
        return render(request, "core/teacher_login.html", {"error": "Invalid name or PIN."})

    request.session["teacher_id"] = teacher.id
    request.session["teacher_name"] = teacher.full_name
    request.session["teacher_logged_in_at"] = timezone.now().isoformat()
    return redirect("/teacher/")


@teacher_required
def teacher_dashboard_page(request: HttpRequest):
    context = _build_dashboard_analytics_context(request)
    context["active"] = "dashboard"
    return render(request, "core/teacher/dashboard.html", context)


@teacher_required
def teacher_sessions_page(request: HttpRequest):
    return render(request, "core/teacher/sessions.html", {"active": "sessions"})


@teacher_required
def teacher_classes_page(request: HttpRequest):
    return render(request, "core/teacher/classes.html", {"active": "classes"})


@teacher_required
def teacher_students_page(request: HttpRequest):
    return render(request, "core/teacher/students.html", {"active": "students"})


@teacher_required
def teacher_tasks_page(request: HttpRequest):
    return render(request, "core/teacher/tasks.html", {"active": "tasks"})
@teacher_required
def teacher_modules_page(request: HttpRequest):
    return render(request, "core/teacher/modules.html", {"active": "modules"})


def healthz(request: HttpRequest):
    return HttpResponse("ok", content_type="text/plain")


# -------------------------
# Teacher classes API
# -------------------------

@csrf_exempt
@require_http_methods(["GET", "POST"])
def teacher_classes_api(request: HttpRequest):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    if request.method == "GET":
        classes = ClassGroup.objects.all().order_by("name")
        return JsonResponse({"ok": True, "classes": [_serialize_class_group(c) for c in classes]})

    data = _json_body(request)
    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "name is required"}, status=400)
    if ClassGroup.objects.filter(name__iexact=name).exists():
        return JsonResponse({"ok": False, "error": "class with this name already exists"}, status=409)

    obj = ClassGroup.objects.create(name=name)
    return JsonResponse({"ok": True, "class": _serialize_class_group(obj)}, status=201)


@csrf_exempt
@require_http_methods(["PATCH", "DELETE"])
def teacher_class_detail_api(request: HttpRequest, class_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    obj = get_object_or_404(ClassGroup, id=class_id)

    if request.method == "PATCH":
        data = _json_body(request)
        name = (data.get("name") or "").strip()
        if not name:
            return JsonResponse({"ok": False, "error": "name is required"}, status=400)
        if ClassGroup.objects.exclude(id=obj.id).filter(name__iexact=name).exists():
            return JsonResponse({"ok": False, "error": "class with this name already exists"}, status=409)
        obj.name = name
        obj.save(update_fields=["name"])
        return JsonResponse({"ok": True, "class": _serialize_class_group(obj)})

    if Student.objects.filter(class_group=obj).exists():
        return JsonResponse({"ok": False, "error": "cannot delete: class has students"}, status=409)

    obj.delete()
    return JsonResponse({"ok": True})


# -------------------------
# Teacher students API
# -------------------------

@csrf_exempt
@require_http_methods(["GET", "POST"])
def teacher_students_api(request: HttpRequest):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    if request.method == "GET":
        class_id = request.GET.get("class_id") or ""
        qs = Student.objects.select_related("class_group").order_by("class_group__name", "full_name")
        if class_id.isdigit():
            qs = qs.filter(class_group_id=int(class_id))
        return JsonResponse({"ok": True, "students": [_serialize_student(s) for s in qs]})

    data = _json_body(request)
    full_name = (data.get("full_name") or "").strip()
    pin = str(data.get("pin") or "").strip()
    class_id = data.get("class_id")

    if not full_name:
        return JsonResponse({"ok": False, "error": "full_name is required"}, status=400)
    if not PIN_RE.match(pin):
        return JsonResponse({"ok": False, "error": "pin must be 6 digits"}, status=400)
    if not (isinstance(class_id, int) or (isinstance(class_id, str) and str(class_id).isdigit())):
        return JsonResponse({"ok": False, "error": "class_id is required"}, status=400)
    if Student.objects.filter(full_name__iexact=full_name).exists():
        return JsonResponse({"ok": False, "error": "student with this name already exists"}, status=409)

    cg = get_object_or_404(ClassGroup, id=int(class_id))
    st = Student(full_name=full_name, class_group=cg, is_active=True)
    st.set_pin(pin)
    st.save()
    return JsonResponse({"ok": True, "student": _serialize_student(st)}, status=201)


@csrf_exempt
@require_http_methods(["PATCH", "DELETE"])
def teacher_student_detail_api(request: HttpRequest, student_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    st = get_object_or_404(Student.objects.select_related("class_group"), id=student_id)

    if request.method == "DELETE":
        st.delete()
        return JsonResponse({"ok": True})

    data = _json_body(request)

    if "full_name" in data:
        name = (data.get("full_name") or "").strip()
        if not name:
            return JsonResponse({"ok": False, "error": "full_name cannot be empty"}, status=400)
        if Student.objects.exclude(id=st.id).filter(full_name__iexact=name).exists():
            return JsonResponse({"ok": False, "error": "student with this name already exists"}, status=409)
        st.full_name = name

    if "class_id" in data:
        cid = data.get("class_id")
        if not (isinstance(cid, int) or (isinstance(cid, str) and str(cid).isdigit())):
            return JsonResponse({"ok": False, "error": "invalid class_id"}, status=400)
        st.class_group = get_object_or_404(ClassGroup, id=int(cid))

    if "is_active" in data:
        st.is_active = bool(data.get("is_active"))

    st.save()
    return JsonResponse({"ok": True, "student": _serialize_student(st)})


@csrf_exempt
@require_POST
def teacher_student_reset_pin_api(request: HttpRequest, student_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    st = get_object_or_404(Student, id=student_id)
    data = _json_body(request)
    pin = str(data.get("pin") or "").strip()
    if not PIN_RE.match(pin):
        return JsonResponse({"ok": False, "error": "pin must be 6 digits"}, status=400)

    st.set_pin(pin)
    st.save(update_fields=["pin_hash"])
    return JsonResponse({"ok": True})


# -------------------------
# Teacher sessions API
# -------------------------
@csrf_exempt
@require_http_methods(["GET", "POST"])
def teacher_sessions_api(request: HttpRequest):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    try:
        if request.method == "GET":
            sessions = Session.objects.all().order_by("-created_at")
            available_classes = list(
                ClassGroup.objects.order_by("name").values("id", "name")
            )
            return JsonResponse({
                "ok": True,
                "sessions": [_serialize_session(s) for s in sessions],
                "available_classes": available_classes,
            })

        data = _json_body(request)

        title = (data.get("title") or "").strip()
        if not title:
            return JsonResponse({"ok": False, "error": "title is required"}, status=400)

        status = _normalize_session_status_in(data.get("status") or SESSION_STATUS_DRAFT)
        if status not in SESSION_STATUSES:
            return JsonResponse({"ok": False, "error": "invalid status"}, status=400)

        starts_at = _parse_dt_or_none(data.get("starts_at") or "")
        ends_at = _parse_dt_or_none(data.get("ends_at") or "")
        if starts_at and ends_at and ends_at <= starts_at:
            return JsonResponse({"ok": False, "error": "ends_at must be after starts_at"}, status=400)

        class_ids = data.get("class_group_ids") or []
        if not isinstance(class_ids, list):
            return JsonResponse({"ok": False, "error": "class_group_ids must be a list"}, status=400)

        with transaction.atomic():
            session = Session.objects.create(
                title=title,
                description=(data.get("description") or ""),
                status=status,
                starts_at=starts_at,
                ends_at=ends_at,
            )

            clean_ids = []
            for cid in class_ids:
                try:
                    clean_ids.append(int(cid))
                except (TypeError, ValueError):
                    return JsonResponse({"ok": False, "error": "class_group_ids must contain integers"}, status=400)

            if clean_ids:
                existing_ids = set(
                    ClassGroup.objects.filter(id__in=clean_ids).values_list("id", flat=True)
                )
                missing = [cid for cid in clean_ids if cid not in existing_ids]
                if missing:
                    return JsonResponse(
                        {"ok": False, "error": f"Some classes do not exist: {missing}"},
                        status=400,
                    )

                SessionClass.objects.bulk_create(
                    [SessionClass(session=session, class_group_id=cid) for cid in clean_ids]
                )

        return JsonResponse({"ok": True, "session": _serialize_session(session)}, status=201)

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)
@csrf_exempt
@require_http_methods(["GET", "POST"])
def teacher_session_tasks_api(request: HttpRequest, session_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    session = get_object_or_404(Session, id=session_id)

    try:
        if request.method == "GET":
            tasks = SessionTask.objects.filter(session=session).order_by("position", "id")
            return JsonResponse({
                "ok": True,
                "tasks": [_serialize_task(t) for t in tasks],
            })

        data = _json_body(request)

        title = (data.get("title") or "").strip()
        if not title:
            return JsonResponse({"ok": False, "error": "title is required"}, status=400)

        try:
            position = int(data.get("position") or 1)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "position must be integer"}, status=400)

        if position < 1:
            return JsonResponse({"ok": False, "error": "position must be >= 1"}, status=400)

        statement = data.get("statement") or ""
        constraints = data.get("constraints") or ""
        programming_language = str(
            data.get("programming_language") or SessionTask.ProgrammingLanguage.PYTHON
        ).strip()
        if programming_language not in {
            SessionTask.ProgrammingLanguage.PYTHON,
            SessionTask.ProgrammingLanguage.CPP,
        }:
            return JsonResponse({"ok": False, "error": "invalid programming_language"}, status=400)

        task = SessionTask.objects.create(
            session=session,
            position=position,
            title=title,
            statement=statement,
            constraints=constraints,
            programming_language=programming_language,
        )

        return JsonResponse({
            "ok": True,
            "task": _serialize_task(task),
        }, status=201)

    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": f"{type(e).__name__}: {e}"},
            status=500
        )


@csrf_exempt
@require_http_methods(["PATCH", "DELETE"])
@csrf_exempt
@require_http_methods(["PATCH", "DELETE"])
def teacher_session_detail_api(request: HttpRequest, session_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    s = get_object_or_404(Session, id=session_id)

    try:
        if request.method == "DELETE":
            if SessionTask.objects.filter(session=s).exists():
                return JsonResponse({"ok": False, "error": "cannot delete session with tasks"}, status=409)
            if StudentSession.objects.filter(session=s).exists():
                return JsonResponse({"ok": False, "error": "cannot delete session with student activity"}, status=409)
            s.delete()
            return JsonResponse({"ok": True})

        data = _json_body(request)

        if "title" in data:
            title = (data.get("title") or "").strip()
            if not title:
                return JsonResponse({"ok": False, "error": "title cannot be empty"}, status=400)
            s.title = title

        if "description" in data:
            s.description = data.get("description") or ""

        if "starts_at" in data:
            s.starts_at = _parse_dt_or_none(data.get("starts_at") or "")

        if "ends_at" in data:
            s.ends_at = _parse_dt_or_none(data.get("ends_at") or "")

        if "status" in data:
            status = _normalize_session_status_in(data.get("status") or "")
            if status not in SESSION_STATUSES:
                return JsonResponse({"ok": False, "error": "invalid status"}, status=400)
            s.status = status

        if s.starts_at and s.ends_at and s.ends_at <= s.starts_at:
            return JsonResponse({"ok": False, "error": "ends_at must be after starts_at"}, status=400)

        if "class_group_ids" in data:
            class_ids = data.get("class_group_ids") or []
            if not isinstance(class_ids, list):
                return JsonResponse({"ok": False, "error": "class_group_ids must be a list"}, status=400)

            clean_ids = []
            for cid in class_ids:
                try:
                    clean_ids.append(int(cid))
                except (TypeError, ValueError):
                    return JsonResponse({"ok": False, "error": "class_group_ids must contain integers"}, status=400)

            existing_ids = set(ClassGroup.objects.filter(id__in=clean_ids).values_list("id", flat=True))
            missing = [cid for cid in clean_ids if cid not in existing_ids]
            if missing:
                return JsonResponse({"ok": False, "error": f"Some classes do not exist: {missing}"}, status=400)

            with transaction.atomic():
                s.save()
                SessionClass.objects.filter(session=s).delete()
                SessionClass.objects.bulk_create(
                    [SessionClass(session=s, class_group_id=cid) for cid in clean_ids]
                )

            return JsonResponse({"ok": True, "session": _serialize_session(s)})

        s.save()
        return JsonResponse({"ok": True, "session": _serialize_session(s)})

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)


@require_GET
def teacher_session_classes_api(request: HttpRequest, session_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()
    session = get_object_or_404(Session, id=session_id)
    class_ids = list(SessionClass.objects.filter(session=session).values_list("class_group_id", flat=True))
    return JsonResponse({"ok": True, "class_ids": class_ids})


@csrf_exempt
@require_POST
@csrf_exempt
@require_POST
def teacher_session_assign_classes_api(request: HttpRequest, session_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    session = get_object_or_404(Session, id=session_id)

    try:
        data = _json_body(request)
        raw_ids = data.get("class_ids") or data.get("class_group_ids") or []

        if not isinstance(raw_ids, list):
            return JsonResponse({"ok": False, "error": "class_ids must be a list"}, status=400)

        clean_ids = []
        for cid in raw_ids:
            try:
                clean_ids.append(int(cid))
            except (TypeError, ValueError):
                return JsonResponse({"ok": False, "error": "class_ids must contain integers"}, status=400)

        existing_ids = set(ClassGroup.objects.filter(id__in=clean_ids).values_list("id", flat=True))
        missing = [cid for cid in clean_ids if cid not in existing_ids]
        if missing:
            return JsonResponse({"ok": False, "error": f"Some classes do not exist: {missing}"}, status=400)

        with transaction.atomic():
            SessionClass.objects.filter(session=session).delete()
            SessionClass.objects.bulk_create(
                [SessionClass(session=session, class_group_id=cid) for cid in clean_ids]
            )

        return JsonResponse({"ok": True, "class_ids": clean_ids})

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)


# -------------------------
# Teacher tasks / tests / fragments API
# -------------------------



@csrf_exempt
@require_http_methods(["GET", "PATCH", "DELETE"])
def teacher_task_detail_api(request: HttpRequest, task_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    task = get_object_or_404(SessionTask, id=task_id)

    if request.method == "GET":
        return JsonResponse({"ok": True, "task": _serialize_task(task)})

    if request.method == "DELETE":
        task.delete()
        return JsonResponse({"ok": True})

    data = _json_body(request)
    if "position" in data:
        try:
            task.position = int(data.get("position") or 1)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "position must be integer"}, status=400)
    if "title" in data:
        title = (data.get("title") or "").strip()
        if not title:
            return JsonResponse({"ok": False, "error": "title cannot be empty"}, status=400)
        task.title = title
    if "statement" in data:
        task.statement = data.get("statement") or ""
    if "constraints" in data:
        task.constraints = data.get("constraints") or ""
    if "programming_language" in data:
        programming_language = str(data.get("programming_language") or "").strip()
        if programming_language not in {
            SessionTask.ProgrammingLanguage.PYTHON,
            SessionTask.ProgrammingLanguage.CPP,
        }:
            return JsonResponse({"ok": False, "error": "invalid programming_language"}, status=400)
        task.programming_language = programming_language

    task.save()
    return JsonResponse({"ok": True, "task": _serialize_task(task)})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def teacher_task_tests_api(request: HttpRequest, task_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    task = get_object_or_404(SessionTask, id=task_id)

    if request.method == "GET":
        tests = TaskTestCase.objects.filter(task=task).order_by("ordinal", "id")
        return JsonResponse({"ok": True, "tests": [_serialize_testcase(t) for t in tests]})

    data = _json_body(request)
    try:
        ordinal = int(data.get("ordinal") or 1)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "ordinal must be integer"}, status=400)

    tc = TaskTestCase.objects.create(
        task=task,
        ordinal=ordinal,
        stdin=data.get("stdin") or "",
        expected_stdout=data.get("expected_stdout") or "",
        is_visible=bool(data.get("is_visible", False)),
    )
    return JsonResponse({"ok": True, "test": _serialize_testcase(tc)}, status=201)


@csrf_exempt
@require_http_methods(["PATCH", "DELETE"])
def teacher_test_detail_api(request: HttpRequest, test_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    tc = get_object_or_404(TaskTestCase, id=test_id)

    if request.method == "DELETE":
        tc.delete()
        return JsonResponse({"ok": True})

    data = _json_body(request)
    if "ordinal" in data:
        try:
            tc.ordinal = int(data.get("ordinal") or 1)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "ordinal must be integer"}, status=400)
    if "stdin" in data:
        tc.stdin = data.get("stdin") or ""
    if "expected_stdout" in data:
        tc.expected_stdout = data.get("expected_stdout") or ""
    if "is_visible" in data:
        tc.is_visible = bool(data.get("is_visible"))

    tc.save()
    return JsonResponse({"ok": True, "test": _serialize_testcase(tc)})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def teacher_task_fragments_api(request: HttpRequest, task_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    task = get_object_or_404(SessionTask, id=task_id)

    if request.method == "GET":
        frags = TaskCodeFragment.objects.filter(task=task).order_by("position", "id")
        return JsonResponse({"ok": True, "fragments": [_serialize_fragment(f) for f in frags]})

    data = _json_body(request)
    position = (data.get("position") or "").strip()
    if position not in {TaskCodeFragment.Position.TOP, TaskCodeFragment.Position.BOTTOM}:
        return JsonResponse({"ok": False, "error": "invalid position"}, status=400)
    code = data.get("code") or ""
    if not str(code).strip():
        return JsonResponse({"ok": False, "error": "code is required"}, status=400)

    frag = TaskCodeFragment.objects.create(
        task=task,
        position=position,
        title=(data.get("title") or "").strip(),
        code=code,
        is_active=bool(data.get("is_active", True)),
    )
    return JsonResponse({"ok": True, "fragment": _serialize_fragment(frag)}, status=201)

@csrf_exempt
@require_http_methods(["GET", "POST"])
def teacher_theory_modules_api(request: HttpRequest, session_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    session = get_object_or_404(Session, id=session_id)

    try:
        if request.method == "GET":
            modules = (
                TheoryMaterialModule.objects
                .filter(session=session)
                .prefetch_related("blocks")
                .order_by("position", "id")
            )
            return JsonResponse({
                "ok": True,
                "modules": [_serialize_theory_module(m) for m in modules],
            })

        data = _json_body(request)

        title = (data.get("title") or "").strip()
        topic = (data.get("topic") or "").strip()
        ai_prompt = (data.get("ai_prompt") or "").strip()

        if not title:
            return JsonResponse({"ok": False, "error": "title is required"}, status=400)

        try:
            position = int(data.get("position") or 1)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "position must be integer"}, status=400)

        if position < 1:
            return JsonResponse({"ok": False, "error": "position must be >= 1"}, status=400)

        if _is_module_position_taken(session, position):
            return JsonResponse(
                {"ok": False, "error": "position is already used by another module"},
                status=409,
            )

        module = TheoryMaterialModule.objects.create(
            session=session,
            position=position,
            title=title,
            topic=topic,
            ai_prompt=ai_prompt,
        )

        return JsonResponse({"ok": True, "module": _serialize_theory_module(module)}, status=201)

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)


@csrf_exempt
@require_http_methods(["GET", "PATCH", "DELETE"])
def teacher_theory_module_detail_api(request: HttpRequest, module_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    module = get_object_or_404(TheoryMaterialModule.objects.prefetch_related("blocks"), id=module_id)

    try:
        if request.method == "GET":
            return JsonResponse({"ok": True, "module": _serialize_theory_module(module)})

        if request.method == "DELETE":
            module.delete()
            return JsonResponse({"ok": True})

        data = _json_body(request)

        if "title" in data:
            title = (data.get("title") or "").strip()
            if not title:
                return JsonResponse({"ok": False, "error": "title cannot be empty"}, status=400)
            module.title = title

        if "topic" in data:
            module.topic = (data.get("topic") or "").strip()

        if "ai_prompt" in data:
            module.ai_prompt = data.get("ai_prompt") or ""

        if "is_active" in data:
            module.is_active = bool(data.get("is_active"))

        if "position" in data:
            try:
                position = int(data.get("position"))
            except (TypeError, ValueError):
                return JsonResponse({"ok": False, "error": "position must be integer"}, status=400)

            if position < 1:
                return JsonResponse({"ok": False, "error": "position must be >= 1"}, status=400)

            if _is_module_position_taken(module.session, position, skip_type="theory_material", skip_id=module.id):
                return JsonResponse(
                    {"ok": False, "error": "position is already used by another module"},
                    status=409,
                )

            module.position = position

        module.save()
        return JsonResponse({"ok": True, "module": _serialize_theory_module(module)})

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def teacher_theory_blocks_api(request: HttpRequest, module_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    module = get_object_or_404(TheoryMaterialModule, id=module_id)

    try:
        if request.method == "GET":
            blocks = TheoryMaterialBlock.objects.filter(module=module).order_by("ordinal", "id")
            return JsonResponse({
                "ok": True,
                "blocks": [_serialize_theory_block(b) for b in blocks],
            })

        data = _json_body(request)

        block_type = (data.get("block_type") or "").strip()
        heading_level = (data.get("heading_level") or "").strip()
        content = (data.get("content") or "").rstrip()

        try:
            ordinal = int(data.get("ordinal") or 1)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "ordinal must be integer"}, status=400)

        if ordinal < 1:
            return JsonResponse({"ok": False, "error": "ordinal must be >= 1"}, status=400)

        if block_type not in {
            TheoryMaterialBlock.BlockType.HEADING,
            TheoryMaterialBlock.BlockType.TEXT,
            TheoryMaterialBlock.BlockType.CODE,
        }:
            return JsonResponse({"ok": False, "error": "invalid block_type"}, status=400)

        if not content.strip():
            return JsonResponse({"ok": False, "error": "content is required"}, status=400)

        if block_type == TheoryMaterialBlock.BlockType.HEADING:
            if heading_level not in {TheoryMaterialBlock.HeadingLevel.H1, TheoryMaterialBlock.HeadingLevel.H2}:
                return JsonResponse({"ok": False, "error": "heading_level must be h1 or h2"}, status=400)
        else:
            heading_level = ""

        if TheoryMaterialBlock.objects.filter(module=module, ordinal=ordinal).exists():
            return JsonResponse({"ok": False, "error": "ordinal is already used"}, status=409)

        block = TheoryMaterialBlock.objects.create(
            module=module,
            ordinal=ordinal,
            block_type=block_type,
            heading_level=heading_level,
            content=content,
        )

        return JsonResponse({"ok": True, "block": _serialize_theory_block(block)}, status=201)

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)


@csrf_exempt
@require_http_methods(["PATCH", "DELETE"])
def teacher_theory_block_detail_api(request: HttpRequest, block_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    block = get_object_or_404(TheoryMaterialBlock, id=block_id)

    try:
        if request.method == "DELETE":
            block.delete()
            return JsonResponse({"ok": True})

        data = _json_body(request)

        if "ordinal" in data:
            try:
                ordinal = int(data.get("ordinal"))
            except (TypeError, ValueError):
                return JsonResponse({"ok": False, "error": "ordinal must be integer"}, status=400)

            if ordinal < 1:
                return JsonResponse({"ok": False, "error": "ordinal must be >= 1"}, status=400)

            conflict = TheoryMaterialBlock.objects.filter(
                module=block.module,
                ordinal=ordinal,
            ).exclude(id=block.id).exists()

            if conflict:
                return JsonResponse({"ok": False, "error": "ordinal is already used"}, status=409)

            block.ordinal = ordinal

        if "block_type" in data:
            block_type = (data.get("block_type") or "").strip()
            if block_type not in {
                TheoryMaterialBlock.BlockType.HEADING,
                TheoryMaterialBlock.BlockType.TEXT,
                TheoryMaterialBlock.BlockType.CODE,
            }:
                return JsonResponse({"ok": False, "error": "invalid block_type"}, status=400)
            block.block_type = block_type

        if "heading_level" in data:
            heading_level = (data.get("heading_level") or "").strip()
            if block.block_type == TheoryMaterialBlock.BlockType.HEADING:
                if heading_level not in {TheoryMaterialBlock.HeadingLevel.H1, TheoryMaterialBlock.HeadingLevel.H2}:
                    return JsonResponse({"ok": False, "error": "heading_level must be h1 or h2"}, status=400)
                block.heading_level = heading_level
            else:
                block.heading_level = ""

        if "content" in data:
            content = (data.get("content") or "").rstrip()
            if not content.strip():
                return JsonResponse({"ok": False, "error": "content cannot be empty"}, status=400)
            block.content = content

        if block.block_type != TheoryMaterialBlock.BlockType.HEADING:
            block.heading_level = ""

        block.save()
        return JsonResponse({"ok": True, "block": _serialize_theory_block(block)})

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)


@csrf_exempt
@require_POST
def teacher_generate_theory_module_api(request: HttpRequest, module_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    module = get_object_or_404(TheoryMaterialModule.objects.select_related("session"), id=module_id)

    try:
        data = _json_body(request)
        prompt = (data.get("prompt") or module.ai_prompt or "").strip()

        if not prompt:
            return JsonResponse({"ok": False, "error": "prompt is required"}, status=400)

        prompt_snapshot = build_theory_material_prompt_snapshot(
            session_title=module.session.title,
            session_description=module.session.description,
            module_title=module.title,
            topic=module.topic,
            teacher_prompt=prompt,
        )

        out = call_openai_theory_material(prompt_snapshot)
        payload = out.get("data") or {}
        title = (payload.get("title") or "").strip()
        blocks = payload.get("blocks") or []

        with transaction.atomic():
            if title:
                module.title = title
            module.ai_prompt = prompt
            module.save(update_fields=["title", "ai_prompt", "updated_at"])

            TheoryMaterialBlock.objects.filter(module=module).delete()

            clean_blocks = []
            used_ordinals = set()

            for raw in blocks:
                ordinal = int(raw.get("ordinal") or 0)
                block_type = (raw.get("block_type") or "").strip()
                heading_level = (raw.get("heading_level") or "").strip()
                content = (raw.get("content") or "").rstrip()

                if ordinal < 1 or ordinal in used_ordinals or not content:
                    continue

                if block_type not in {"heading", "text", "code"}:
                    continue

                if block_type == "heading" and heading_level not in {"h1", "h2"}:
                    heading_level = "h2"
                if block_type != "heading":
                    heading_level = ""

                used_ordinals.add(ordinal)
                clean_blocks.append(
                    TheoryMaterialBlock(
                        module=module,
                        ordinal=ordinal,
                        block_type=block_type,
                        heading_level=heading_level,
                        content=content,
                    )
                )

            if not clean_blocks:
                return JsonResponse({"ok": False, "error": "AI returned no valid blocks"}, status=502)

            TheoryMaterialBlock.objects.bulk_create(clean_blocks)

        module = TheoryMaterialModule.objects.prefetch_related("blocks").get(id=module.id)
        return JsonResponse({"ok": True, "module": _serialize_theory_module(module)})

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def teacher_theory_quizzes_api(request: HttpRequest, session_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    session = get_object_or_404(Session, id=session_id)

    try:
        if request.method == "GET":
            modules = (
                TheoryQuizModule.objects
                .filter(session=session)
                .prefetch_related("questions__choices", "questions__pairs")
                .order_by("position", "id")
            )
            return JsonResponse({"ok": True, "modules": [_serialize_theory_quiz_module(m) for m in modules]})

        data = _json_body(request)
        title = (data.get("title") or "").strip()
        topic = (data.get("topic") or "").strip()
        instructions = (data.get("instructions") or "").strip()

        if not title:
            return JsonResponse({"ok": False, "error": "title is required"}, status=400)

        try:
            position = int(data.get("position") or 1)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "position must be integer"}, status=400)

        if position < 1:
            return JsonResponse({"ok": False, "error": "position must be >= 1"}, status=400)

        if _is_module_position_taken(session, position):
            return JsonResponse({"ok": False, "error": "position is already used by another module"}, status=409)

        module = TheoryQuizModule.objects.create(
            session=session,
            position=position,
            title=title,
            topic=topic,
            instructions=instructions,
        )
        return JsonResponse({"ok": True, "module": _serialize_theory_quiz_module(module)}, status=201)

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)


@csrf_exempt
@require_http_methods(["GET", "PATCH", "DELETE"])
def teacher_theory_quiz_detail_api(request: HttpRequest, module_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    module = get_object_or_404(
        TheoryQuizModule.objects.prefetch_related("questions__choices", "questions__pairs"),
        id=module_id,
    )

    try:
        if request.method == "GET":
            return JsonResponse({"ok": True, "module": _serialize_theory_quiz_module(module)})

        if request.method == "DELETE":
            module.delete()
            return JsonResponse({"ok": True})

        data = _json_body(request)
        if "title" in data:
            title = (data.get("title") or "").strip()
            if not title:
                return JsonResponse({"ok": False, "error": "title cannot be empty"}, status=400)
            module.title = title
        if "topic" in data:
            module.topic = (data.get("topic") or "").strip()
        if "instructions" in data:
            module.instructions = data.get("instructions") or ""
        if "is_active" in data:
            module.is_active = bool(data.get("is_active"))
        if "position" in data:
            try:
                position = int(data.get("position"))
            except (TypeError, ValueError):
                return JsonResponse({"ok": False, "error": "position must be integer"}, status=400)
            if position < 1:
                return JsonResponse({"ok": False, "error": "position must be >= 1"}, status=400)
            if _is_module_position_taken(module.session, position, skip_type="theory_quiz", skip_id=module.id):
                return JsonResponse({"ok": False, "error": "position is already used by another module"}, status=409)
            module.position = position

        module.save()
        module = TheoryQuizModule.objects.prefetch_related("questions__choices", "questions__pairs").get(id=module.id)
        return JsonResponse({"ok": True, "module": _serialize_theory_quiz_module(module)})

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def teacher_theory_quiz_questions_api(request: HttpRequest, module_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    module = get_object_or_404(TheoryQuizModule, id=module_id)

    try:
        if request.method == "GET":
            questions = (
                TheoryQuizQuestion.objects.filter(module=module)
                .prefetch_related("choices", "pairs")
                .order_by("ordinal", "id")
            )
            return JsonResponse({"ok": True, "questions": [_serialize_theory_quiz_question(q) for q in questions]})

        data = _json_body(request)
        try:
            ordinal = int(data.get("ordinal") or 1)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "ordinal must be integer"}, status=400)
        if ordinal < 1:
            return JsonResponse({"ok": False, "error": "ordinal must be >= 1"}, status=400)
        if TheoryQuizQuestion.objects.filter(module=module, ordinal=ordinal).exists():
            return JsonResponse({"ok": False, "error": "ordinal is already used"}, status=409)

        parsed, error = _parse_theory_quiz_question_payload(data)
        if error:
            return error

        with transaction.atomic():
            question = TheoryQuizQuestion.objects.create(
                module=module,
                ordinal=ordinal,
                question_type=parsed["question_type"],
                prompt=parsed["prompt"],
                model_answer=parsed["model_answer"],
                accept_suitable_answer=parsed["accept_suitable_answer"],
            )

            if parsed["choices"]:
                TheoryQuizChoice.objects.bulk_create([
                    TheoryQuizChoice(question=question, **row) for row in parsed["choices"]
                ])
            if parsed["pairs"]:
                TheoryQuizMatchPair.objects.bulk_create([
                    TheoryQuizMatchPair(question=question, **row) for row in parsed["pairs"]
                ])

        question = TheoryQuizQuestion.objects.prefetch_related("choices", "pairs").get(id=question.id)
        return JsonResponse({"ok": True, "question": _serialize_theory_quiz_question(question)}, status=201)

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)


@csrf_exempt
@require_http_methods(["PATCH", "DELETE"])
def teacher_theory_quiz_question_detail_api(request: HttpRequest, question_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    question = get_object_or_404(
        TheoryQuizQuestion.objects.select_related("module").prefetch_related("choices", "pairs"),
        id=question_id,
    )

    try:
        if request.method == "DELETE":
            question.delete()
            return JsonResponse({"ok": True})

        data = _json_body(request)
        if "ordinal" in data:
            try:
                ordinal = int(data.get("ordinal"))
            except (TypeError, ValueError):
                return JsonResponse({"ok": False, "error": "ordinal must be integer"}, status=400)
            if ordinal < 1:
                return JsonResponse({"ok": False, "error": "ordinal must be >= 1"}, status=400)
            conflict = TheoryQuizQuestion.objects.filter(module=question.module, ordinal=ordinal).exclude(id=question.id).exists()
            if conflict:
                return JsonResponse({"ok": False, "error": "ordinal is already used"}, status=409)
            question.ordinal = ordinal

        merged = {
            "question_type": data.get("question_type", question.question_type),
            "prompt": data.get("prompt", question.prompt),
            "model_answer": data.get("model_answer", question.model_answer),
            "accept_suitable_answer": data.get("accept_suitable_answer", question.accept_suitable_answer),
            "choices": data.get("choices", [
                {"text": x.text, "is_correct": x.is_correct} for x in question.choices.all().order_by("ordinal", "id")
            ]),
            "pairs": data.get("pairs", [
                {"left_text": x.left_text, "right_text": x.right_text} for x in question.pairs.all().order_by("ordinal", "id")
            ]),
        }

        parsed, error = _parse_theory_quiz_question_payload(merged)
        if error:
            return error

        with transaction.atomic():
            question.question_type = parsed["question_type"]
            question.prompt = parsed["prompt"]
            question.model_answer = parsed["model_answer"]
            question.accept_suitable_answer = parsed["accept_suitable_answer"]
            question.save()

            TheoryQuizChoice.objects.filter(question=question).delete()
            TheoryQuizMatchPair.objects.filter(question=question).delete()

            if parsed["choices"]:
                TheoryQuizChoice.objects.bulk_create([
                    TheoryQuizChoice(question=question, **row) for row in parsed["choices"]
                ])
            if parsed["pairs"]:
                TheoryQuizMatchPair.objects.bulk_create([
                    TheoryQuizMatchPair(question=question, **row) for row in parsed["pairs"]
                ])

        question = TheoryQuizQuestion.objects.prefetch_related("choices", "pairs").get(id=question.id)
        return JsonResponse({"ok": True, "question": _serialize_theory_quiz_question(question)})

    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)

@csrf_exempt
@require_http_methods(["PATCH", "DELETE"])
def teacher_fragment_detail_api(request: HttpRequest, frag_id: int):
    if not _get_logged_in_teacher(request):
        return _teacher_api_unauthorized()

    frag = get_object_or_404(TaskCodeFragment, id=frag_id)

    if request.method == "DELETE":
        frag.delete()
        return JsonResponse({"ok": True})

    data = _json_body(request)
    if "position" in data:
        position = (data.get("position") or "").strip()
        if position not in {TaskCodeFragment.Position.TOP, TaskCodeFragment.Position.BOTTOM}:
            return JsonResponse({"ok": False, "error": "invalid position"}, status=400)
        frag.position = position
    if "title" in data:
        frag.title = (data.get("title") or "").strip()
    if "code" in data:
        code = data.get("code") or ""
        if not str(code).strip():
            return JsonResponse({"ok": False, "error": "code is required"}, status=400)
        frag.code = code
    if "is_active" in data:
        frag.is_active = bool(data.get("is_active"))

    frag.save()
    return JsonResponse({"ok": True, "fragment": _serialize_fragment(frag)})


# -------------------------
# UI language
# -------------------------

@require_POST
def set_ui_language(request):
    lang = (request.POST.get("lang") or "").strip()
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/teacher/"

    if lang not in SUPPORTED_UI_LANGS:
        lang = "ru"

    request.session["ui_lang"] = lang
    request.session.modified = True

    response = redirect(next_url)
    response.set_cookie("ui_lang", lang, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return response

def _build_dashboard_analytics_context(request: HttpRequest) -> dict:
    class_id = _get_persisted_dashboard_class_id(request)
    show_success = True
    show_hints = True

    classes = ClassGroup.objects.all().order_by("name")
    students_qs = Student.objects.filter(is_active=True).select_related("class_group").order_by("class_group__name", "full_name")
    if class_id.isdigit():
        students_qs = students_qs.filter(class_group_id=int(class_id))

    sub_qs = Submission.objects.select_related("progress__student_session__session", "progress__student_session__student")
    if class_id.isdigit():
        sub_qs = sub_qs.filter(progress__student_session__student__class_group_id=int(class_id))

    per_session = (
        sub_qs.values("progress__student_session__session_id", "progress__student_session__session__title")
        .annotate(
            total_sub=Count("id"),
            accepted=Count("id", filter=Q(verdict=Submission.Verdict.ACCEPTED)),
        )
        .order_by("progress__student_session__session_id")
    )

    agg_qs = ActivityAggregate.objects.select_related(
        "progress__student_session__session",
        "progress__student_session__student",
    )
    if class_id.isdigit():
        agg_qs = agg_qs.filter(progress__student_session__student__class_group_id=int(class_id))

    per_session_hints = (
        agg_qs.values("progress__student_session__session_id")
        .annotate(
            hint1_sum=Sum("hint1_requests"),
            hint2_sum=Sum("hint2_requests"),
            hint3_sum=Sum("hint3_requests"),
        )
    )
    hints_map = {
        x["progress__student_session__session_id"]: (
            (x.get("hint1_sum") or 0)
            + (x.get("hint2_sum") or 0)
            + (x.get("hint3_sum") or 0)
        )
        for x in per_session_hints
    }

    labels, totals, accepted, hints = [], [], [], []
    for row in per_session:
        sid = row["progress__student_session__session_id"]
        title = row["progress__student_session__session__title"] or f"Session {sid}"
        labels.append(title)
        totals.append(row["total_sub"] or 0)
        accepted.append(row["accepted"] or 0)
        hints.append(hints_map.get(sid, 0))

    other_max = max(accepted + hints) if (accepted or hints) else 1
    totals_scaled, scale_factor = _scale_totals_if_needed(totals, other_max)

    session_chart = {
        "labels": labels,
        "datasets": {"totals": totals_scaled, "accepted": accepted, "hints": hints},
        "scale_factor": scale_factor,
    }

    ss_qs = StudentSession.objects.select_related("student", "session")
    if class_id.isdigit():
        ss_qs = ss_qs.filter(student__class_group_id=int(class_id))

    sessions_count_map = {
        x["student_id"]: x["c"]
        for x in ss_qs.values("student_id").annotate(c=Count("id"))
    }

    sub_per_student = (
        sub_qs.values("progress__student_session__student_id")
        .annotate(
            total_sub=Count("id"),
            accepted=Count("id", filter=Q(verdict=Submission.Verdict.ACCEPTED)),
        )
    )
    sub_map = {
        x["progress__student_session__student_id"]: (x["total_sub"] or 0, x["accepted"] or 0)
        for x in sub_per_student
    }

    hints_per_student = (
        agg_qs.values("progress__student_session__student_id")
        .annotate(
            hint1_sum=Sum("hint1_requests"),
            hint2_sum=Sum("hint2_requests"),
            hint3_sum=Sum("hint3_requests"),
        )
    )
    hints_student_map = {
        x["progress__student_session__student_id"]: (
            (x.get("hint1_sum") or 0)
            + (x.get("hint2_sum") or 0)
            + (x.get("hint3_sum") or 0)
        )
        for x in hints_per_student
    }

    student_cards = []
    for st in students_qs:
        sc = sessions_count_map.get(st.id, 0) or 0
        total_s, acc_s = sub_map.get(st.id, (0, 0))
        hint_s = hints_student_map.get(st.id, 0)
        denom = sc if sc > 0 else 1

        student_cards.append({
            "id": st.id,
            "name": st.full_name,
            "class_name": st.class_group.name if st.class_group_id else "—",
            "sessions_count": sc,
            "avg_total": round(total_s / denom, 2),
            "avg_accepted": round(acc_s / denom, 2),
            "avg_hints": round(hint_s / denom, 2),
        })

    return {
        "classes": classes,
        "selected_class_id": int(class_id) if class_id.isdigit() else None,
        "show_success": show_success,
        "show_hints": show_hints,
        "session_chart_json": json.dumps(session_chart, ensure_ascii=False),
        "student_cards": student_cards,
    }
