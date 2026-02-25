import hashlib
import json
import re
from collections import defaultdict

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import F, Count, Sum, Q
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods, require_GET

from .ai_assist import build_prompt_snapshot, call_openai_hint, sanitize_no_code
from .judge0_client import create_batch_submissions, wait_batch
from .models import (
    Student,
    Session,
    SessionTask,
    TaskTestCase,
    StudentSession,
    StudentTaskProgress,
    Submission,
    AiAssistMessage,
    ActivityAggregate,
    ClassGroup,
    TaskCodeFragment,   # <-- NEW
)

PIN_RE = re.compile(r"^\d{6}$")


def _json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def _require_student(request: HttpRequest):
    student_id = request.session.get("student_id")
    return student_id or None


def _get_student_from_session(request: HttpRequest):
    student_id = request.session.get("student_id")
    class_id = request.session.get("student_class_id")
    if not student_id or not class_id:
        return None, None
    return student_id, class_id


def _get_active_session_for_class(class_id: int):
    session = (
        Session.objects.filter(status=Session.Status.RUNNING, allowed_classes__id=class_id)
        .distinct()
        .order_by("-starts_at", "-created_at")
        .first()
    )
    if not session or not session.is_active_now():
        return None
    return session


def _get_task_fragments(task: SessionTask):
    """
    Returns (top_fragment, bottom_fragment) as strings.
    If multiple fragments exist per position -> concatenates them in creation order.
    """
    frags = list(
        TaskCodeFragment.objects
        .filter(task=task, is_active=True)
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
    """
    Final code = top + user + bottom.
    Ensures clean newlines between blocks.
    """
    parts = []
    if top_block.strip():
        parts.append(top_block.rstrip() + "\n")
    parts.append((user_code or "").rstrip() + "\n")
    if bottom_block.strip():
        parts.append(bottom_block.rstrip() + "\n")
    return "".join(parts)


# -------------------------
# AUTH API
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


def student_me(request: HttpRequest):
    student_id = request.session.get("student_id")
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
# STUDENT SESSION API
# -------------------------

def student_active_session(request: HttpRequest):
    student_id = _require_student(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    class_id = request.session.get("student_class_id")
    if not class_id:
        return JsonResponse({"ok": False, "error": "invalid session"}, status=401)

    now = timezone.now()
    session = (
        Session.objects.filter(status=Session.Status.RUNNING, allowed_classes__id=class_id)
        .distinct()
        .order_by("-starts_at", "-created_at")
        .first()
    )
    if not session or not session.is_active_now():
        return JsonResponse({"ok": True, "active": False, "message": "Current session is inactive"}, status=200)

    ss, created = StudentSession.objects.get_or_create(
        student_id=student_id,
        session=session,
        defaults={"started_at": now, "last_seen_at": now},
    )
    if not created:
        ss.last_seen_at = now
        ss.save(update_fields=["last_seen_at"])

    tasks = list(
        SessionTask.objects.filter(session=session).order_by("position").values("id", "position", "title")
    )

    progress_qs = StudentTaskProgress.objects.filter(student_session=ss).values(
        "task_id", "status", "attempts_total", "attempts_failed"
    )
    progress_map = {p["task_id"]: p for p in progress_qs}

    tasks_out = []
    for t in tasks:
        p = progress_map.get(t["id"])
        tasks_out.append({
            "id": t["id"],
            "position": t["position"],
            "title": t["title"],
            "progress": p or {"status": "not_started", "attempts_total": 0, "attempts_failed": 0},
        })

    return JsonResponse({
        "ok": True,
        "active": True,
        "session": {
            "id": session.id,
            "title": session.title,
            "description": session.description,
            "starts_at": session.starts_at.isoformat() if session.starts_at else None,
            "ends_at": session.ends_at.isoformat() if session.ends_at else None,
        },
        "tasks": tasks_out
    })


# -------------------------
# TASK DETAIL API (includes read-only code fragments)
# -------------------------

@require_GET
def student_task_detail(request: HttpRequest, task_id: int):
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

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

    progress, created = StudentTaskProgress.objects.get_or_create(
        student_session=ss,
        task=task,
        defaults={"status": StudentTaskProgress.Status.IN_PROGRESS, "opened_at": now},
    )
    if not created:
        if not progress.opened_at:
            progress.opened_at = now
        if progress.status == StudentTaskProgress.Status.NOT_STARTED:
            progress.status = StudentTaskProgress.Status.IN_PROGRESS
        progress.save(update_fields=["opened_at", "status"])

    if progress.status == StudentTaskProgress.Status.SOLVED and progress.locked_after_solve:
        return JsonResponse(
            {"ok": True, "locked": True, "message": "Task already solved and locked"},
            status=200
        )

    visible_tests = list(
        TaskTestCase.objects.filter(task=task, is_visible=True)
        .order_by("ordinal")
        .values("ordinal", "stdin", "expected_stdout")
    )

    top_frag, bottom_frag = _get_task_fragments(task)

    return JsonResponse({
        "ok": True,
        "locked": False,
        "task": {
            "id": task.id,
            "position": task.position,
            "title": task.title,
            "statement": task.statement,
            "constraints": task.constraints,
        },
        "progress": {
            "status": progress.status,
            "attempts_total": progress.attempts_total,
            "attempts_failed": progress.attempts_failed,
            "hint1_available": bool(progress.hint1_unlocked_at),
            "hint2_available": bool(progress.hint2_unlocked_at),
        },
        "visible_testcases": visible_tests,
        # NEW: read-only fragments for UI
        "code_fragments": {
            "top": top_frag,
            "bottom": bottom_frag
        }
    })


# -------------------------
# SUBMIT API (prepends/appends read-only fragments before sending to Judge0)
# -------------------------

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

    if progress.status == StudentTaskProgress.Status.SOLVED and progress.locked_after_solve:
        return JsonResponse({"ok": True, "locked": True, "message": "Task already solved"}, status=200)

    # cooldown 15 sec
    if getattr(progress, "last_submit_at", None):
        delta = (now - progress.last_submit_at).total_seconds()
        if delta < 15:
            return JsonResponse({"ok": False, "error": f"Too frequent submits. Wait {int(15 - delta)}s"}, status=429)

    # unchanged code check based on USER code (not including fragments)
    code_hash = hashlib.sha256(user_code.encode("utf-8")).hexdigest()
    if getattr(progress, "last_code_hash", "") and progress.last_code_hash == code_hash:
        return JsonResponse({"ok": False, "error": "No changes in code since last submit"}, status=400)

    progress.attempts_total += 1
    attempt_no = progress.attempts_total

    testcases = list(
        TaskTestCase.objects.filter(task=task).order_by("ordinal")
        .values("ordinal", "stdin", "expected_stdout")
    )
    if not testcases:
        return JsonResponse({"ok": False, "error": "No testcases configured for this task"}, status=500)

    # NEW: merge read-only fragments with student code before sending to judge
    top_frag, bottom_frag = _get_task_fragments(task)
    final_code = _join_code(top_frag, user_code, bottom_frag)

    try:
        tokens = create_batch_submissions(final_code, testcases)  # <-- use final_code
        results = wait_batch(tokens, timeout_sec=30, poll_interval=0.9)
    except Exception as e:
        verdict = Submission.Verdict.RUNTIME_ERROR
        stdout_last = ""
        stderr_last = f"Judge0 error: {type(e).__name__}: {e}"
        passed_tests = 0
        total_tests = len(testcases)

        progress.last_submit_at = now
        progress.last_code_hash = code_hash
        progress.attempts_failed += 1

        if progress.attempts_failed == 5 and not progress.hint1_unlocked_at:
            progress.hint1_unlocked_at = now
        if progress.attempts_failed == 8 and not progress.hint2_unlocked_at:
            progress.hint2_unlocked_at = now

        progress.save(update_fields=[
            "attempts_total", "attempts_failed", "hint1_unlocked_at", "hint2_unlocked_at",
            "last_submit_at", "last_code_hash"
        ])

        sub = Submission.objects.create(
            progress=progress,
            attempt_no=attempt_no,
            code=user_code,  # store only student-written code
            verdict=verdict,
            stdout=stdout_last,
            stderr=stderr_last,
            passed_tests=passed_tests,
            total_tests=total_tests,
        )

        return JsonResponse({
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
            }
        })

    passed = 0
    stdout_last = ""
    stderr_last = ""
    verdict = Submission.Verdict.WRONG_ANSWER

    for r in results:
        stdout_last = r.stdout
        stderr_last = r.stderr or r.compile_output or r.message

        if r.status_id == 3:
            passed += 1
            continue
        if r.status_id == 4:
            verdict = Submission.Verdict.WRONG_ANSWER
            break
        if r.status_id == 5:
            verdict = Submission.Verdict.TIME_LIMIT
            break
        if r.status_id == 6:
            verdict = Submission.Verdict.COMPILATION_ERROR
            break
        if r.status_id >= 7:
            verdict = Submission.Verdict.RUNTIME_ERROR
            break

        verdict = Submission.Verdict.RUNTIME_ERROR
        break

    if passed == len(results):
        verdict = Submission.Verdict.ACCEPTED

    total_tests = len(results)
    passed_tests = passed

    progress.last_submit_at = now
    progress.last_code_hash = code_hash

    if verdict == Submission.Verdict.ACCEPTED:
        progress.status = StudentTaskProgress.Status.SOLVED
        progress.solved_at = now
        progress.locked_after_solve = True
    else:
        progress.attempts_failed += 1
        if progress.attempts_failed == 5 and not progress.hint1_unlocked_at:
            progress.hint1_unlocked_at = now
        if progress.attempts_failed == 8 and not progress.hint2_unlocked_at:
            progress.hint2_unlocked_at = now

    progress.save(update_fields=[
        "attempts_total", "attempts_failed", "status", "solved_at", "locked_after_solve",
        "hint1_unlocked_at", "hint2_unlocked_at",
        "last_submit_at", "last_code_hash"
    ])

    sub = Submission.objects.create(
        progress=progress,
        attempt_no=attempt_no,
        code=user_code,  # store only student-written code
        verdict=verdict,
        stdout=stdout_last,
        stderr=stderr_last,
        passed_tests=passed_tests,
        total_tests=total_tests,
    )

    return JsonResponse({
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
        }
    })


# -------------------------
# HTML pages
# -------------------------

@require_http_methods(["GET", "POST"])
def student_login_page(request: HttpRequest):
    if request.method == "GET":
        return render(request, "core/student_login.html")

    full_name = (request.POST.get("full_name") or "").strip()
    pin = (request.POST.get("pin") or "").strip()

    if not full_name or not pin or not pin.isdigit() or len(pin) != 6:
        return render(request, "core/student_login.html", {"error": "Enter name and PIN (6 digits)."})

    student = (
        Student.objects.select_related("class_group")
        .filter(full_name__iexact=full_name, is_active=True)
        .first()
    )
    if not student or not student.check_pin(pin):
        return render(request, "core/student_login.html", {"error": "Invalid name or PIN."})

    request.session["student_id"] = student.id
    request.session["student_name"] = student.full_name
    request.session["student_class_id"] = student.class_group_id
    request.session["student_logged_in_at"] = timezone.now().isoformat()

    return redirect("/student/")


def student_portal_page(request: HttpRequest):
    if not request.session.get("student_id"):
        return redirect("/student/login/")
    return render(request, "core/student_portal.html")


@require_POST
def student_logout_page(request: HttpRequest):
    request.session.flush()
    return redirect("/student/login/")


# -------------------------
# Hints counters + hint endpoint (unchanged logic; uses OpenAI)
# -------------------------

def _inc_hint_counter(progress: StudentTaskProgress, level: int) -> None:
    ActivityAggregate.objects.get_or_create(progress=progress)
    if level == 1:
        ActivityAggregate.objects.filter(progress=progress).update(
            hint1_requests=F("hint1_requests") + 1
        )
    else:
        ActivityAggregate.objects.filter(progress=progress).update(
            hint2_requests=F("hint2_requests") + 1
        )


def student_hint_level(request: HttpRequest, task_id: int, level: int):
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    if level not in (1, 2):
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

    if level == 1 and progress.attempts_failed < 5:
        return JsonResponse({"ok": False, "error": "hint level 1 not available yet"}, status=403)
    if level == 2 and progress.attempts_failed < 8:
        return JsonResponse({"ok": False, "error": "hint level 2 not available yet"}, status=403)

    if level == 1 and progress.hint1_text:
        _inc_hint_counter(progress, 1)
        return JsonResponse({"ok": True, "level": 1, "text": progress.hint1_text})

    if level == 2 and progress.hint2_text:
        _inc_hint_counter(progress, 2)
        return JsonResponse({"ok": True, "level": 2, "text": progress.hint2_text})

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
        else:
            progress.hint2_text = cached.response_text
            progress.save(update_fields=["hint2_text"])

        _inc_hint_counter(progress, level)
        return JsonResponse({"ok": True, "level": level, "text": cached.response_text})

    visible_tests = list(
        TaskTestCase.objects.filter(task=task, is_visible=True)
        .order_by("ordinal")
        .values("stdin", "expected_stdout")
    )
    last_sub = (
        Submission.objects
        .filter(progress=progress)
        .order_by("-attempt_no")
        .first()
    )
    last_subs = list(Submission.objects.filter(progress=progress).order_by("attempt_no")[:50])

    prompt_snapshot = build_prompt_snapshot(
        level=level,
        statement=task.statement,
        constraints=task.constraints,
        visible_tests=visible_tests,
        last_submission=last_sub,
        last_submissions=last_subs,
    )

    msg = AiAssistMessage.objects.create(
        progress=progress,
        level=level,
        prompt_snapshot=prompt_snapshot,
        status=AiAssistMessage.Status.ERROR,
        error_message="pending",
    )

    try:
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
        return JsonResponse({"ok": True, "level": level, "text": text})

    except Exception as e:
        msg.status = AiAssistMessage.Status.ERROR
        msg.error_message = f"{type(e).__name__}: {e}"
        msg.save(update_fields=["status", "error_message"])
        return JsonResponse({"ok": False, "error": "AI assistant temporarily unavailable"}, status=502)


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
def admin_stats_dashboard(request: HttpRequest) -> HttpResponse:
    class_id = request.GET.get("class_id") or ""
    show_success = request.GET.get("show_success") == "1"
    show_hints = request.GET.get("show_hints") == "1"

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
        .annotate(hint_req=Sum(F("hint1_requests") + F("hint2_requests")))
    )
    hints_map = {x["progress__student_session__session_id"]: (x["hint_req"] or 0) for x in per_session_hints}

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

    sessions_count_map = {x["student_id"]: x["c"] for x in ss_qs.values("student_id").annotate(c=Count("id"))}

    sub_per_student = (
        sub_qs.values("progress__student_session__student_id")
        .annotate(
            total_sub=Count("id"),
            accepted=Count("id", filter=Q(verdict=Submission.Verdict.ACCEPTED)),
        )
    )
    sub_map = {x["progress__student_session__student_id"]: (x["total_sub"] or 0, x["accepted"] or 0) for x in sub_per_student}

    hints_per_student = (
        agg_qs.values("progress__student_session__student_id")
        .annotate(hint_req=Sum(F("hint1_requests") + F("hint2_requests")))
    )
    hints_student_map = {x["progress__student_session__student_id"]: (x["hint_req"] or 0) for x in hints_per_student}

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

    context = {
        "classes": classes,
        "selected_class_id": int(class_id) if class_id.isdigit() else None,
        "show_success": show_success,
        "show_hints": show_hints,
        "session_chart_json": json.dumps(session_chart, ensure_ascii=False),
        "student_cards": student_cards,
    }
    return render(request, "core/admin_stats_dashboard.html", context)


@staff_member_required
def admin_student_profile(request: HttpRequest, student_id: int) -> HttpResponse:
    student = get_object_or_404(Student.objects.select_related("class_group"), id=student_id, is_active=True)

    ss = (
        StudentSession.objects.filter(student=student)
        .select_related("session")
        .order_by("session__starts_at", "session__created_at")
    )

    sub_qs = Submission.objects.filter(progress__student_session__student=student)
    per_session = (
        sub_qs.values("progress__student_session__session_id", "progress__student_session__session__title")
        .annotate(
            total_sub=Count("id"),
            accepted=Count("id", filter=Q(verdict=Submission.Verdict.ACCEPTED)),
        )
    )
    per_map = {x["progress__student_session__session_id"]: x for x in per_session}

    labels, rates, totals, accepts = [], [], [], []
    for row in ss:
        sid = row.session_id
        title = row.session.title or f"Session {sid}"
        x = per_map.get(sid, {"total_sub": 0, "accepted": 0})
        t = int(x.get("total_sub") or 0)
        a = int(x.get("accepted") or 0)
        rate = (a / t) if t > 0 else 0.0

        labels.append(title)
        rates.append(round(rate * 100, 2))
        totals.append(t)
        accepts.append(a)

    chart = {"labels": labels, "rates": rates, "totals": totals, "accepts": accepts}
    return render(request, "core/admin_student_profile.html", {
        "student": student,
        "chart_json": json.dumps(chart, ensure_ascii=False),
    })