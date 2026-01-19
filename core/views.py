import hashlib
import json
import re

from django.http import JsonResponse, HttpRequest
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.views.decorators.http import require_GET
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
    AiAssistMessage
)
PIN_RE = re.compile(r"^\d{6}$")


def _json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


@csrf_exempt  # на MVP, чтобы проще тестировать без фронта. Позже уберём и настроим CSRF.
@require_POST
def student_login(request: HttpRequest):
    """
    POST /api/auth/student-login
    body: {"full_name": "...", "pin": "123456"}
    Result: sets Django session cookie + returns student info
    """
    data = _json_body(request)
    full_name = (data.get("full_name") or "").strip()
    pin = str(data.get("pin") or "").strip()

    if not full_name or not pin:
        return JsonResponse({"ok": False, "error": "full_name and pin are required"}, status=400)

    if not PIN_RE.match(pin):
        return JsonResponse({"ok": False, "error": "pin must be 6 digits"}, status=400)

    # Ищем активного ученика по имени
    student = (
        Student.objects.select_related("class_group")
        .filter(full_name__iexact=full_name, is_active=True)
        .first()
    )

    if not student:
        return JsonResponse({"ok": False, "error": "student not found"}, status=404)

    if not student.check_pin(pin):
        return JsonResponse({"ok": False, "error": "invalid credentials"}, status=401)

    # Пишем в Django session (cookie)
    request.session["student_id"] = student.id
    request.session["student_name"] = student.full_name
    request.session["student_class_id"] = student.class_group_id
    request.session["student_logged_in_at"] = timezone.now().isoformat()

    return JsonResponse(
        {
            "ok": True,
            "student": {
                "id": student.id,
                "full_name": student.full_name,
                "class": {"id": student.class_group_id, "name": student.class_group.name},
            },
        }
    )


@csrf_exempt
@require_POST
def student_logout(request: HttpRequest):
    """
    POST /api/auth/student-logout
    Clears session.
    """
    request.session.flush()
    return JsonResponse({"ok": True})


def student_me(request: HttpRequest):
    """
    GET /api/auth/student-me
    Return current logged in student from session.
    """
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

    return JsonResponse(
        {
            "ok": True,
            "student": {
                "id": student.id,
                "full_name": student.full_name,
                "class": {"id": student.class_group_id, "name": student.class_group.name},
            },
        }
    )



def _require_student(request: HttpRequest):
    student_id = request.session.get("student_id")
    if not student_id:
        return None
    return student_id


def student_active_session(request: HttpRequest):
    """
    GET /api/student/active-session
    """
    student_id = _require_student(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    class_id = request.session.get("student_class_id")
    if not class_id:
        return JsonResponse({"ok": False, "error": "invalid session"}, status=401)

    # Ищем активную running-сессию для класса
    now = timezone.now()
    session = (
        Session.objects.filter(
            status=Session.Status.RUNNING,
            allowed_classes__id=class_id,
        )
        .distinct()
        .order_by("-starts_at", "-created_at")
        .first()
    )

    if not session or not session.is_active_now():
        return JsonResponse(
            {"ok": True, "active": False, "message": "Текущая сессия неактивна"},
            status=200
        )

    # Создаем/получаем StudentSession
    ss, created = StudentSession.objects.get_or_create(
        student_id=student_id,
        session=session,
        defaults={"started_at": now, "last_seen_at": now},
    )
    if not created:
        ss.last_seen_at = now
        ss.save(update_fields=["last_seen_at"])

    # Берём задачи сессии
    tasks = list(
        SessionTask.objects.filter(session=session).order_by("position").values(
            "id", "position", "title"
        )
    )

    # Берём прогресс по задачам (если прогресса нет — создадим на следующем шаге, когда откроют задачу)
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


@require_GET
def student_task_detail(request: HttpRequest, task_id: int):
    """
    GET /api/student/task/<task_id>
    - требует student session cookie
    - проверяет, что задача принадлежит активной сессии ученика
    - создаёт StudentSession и StudentTaskProgress при первом открытии
    - отдаёт statement/constraints + visible testcases
    - если задача solved и locked_after_solve=True -> запрещает просмотр
    """
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse({"ok": True, "active": False, "message": "Текущая сессия неактивна"}, status=200)

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
        defaults={
            "status": StudentTaskProgress.Status.IN_PROGRESS,
            "opened_at": now,
        },
    )
    if not created:
        # отметим, что открыли задачу
        if not progress.opened_at:
            progress.opened_at = now
        if progress.status == StudentTaskProgress.Status.NOT_STARTED:
            progress.status = StudentTaskProgress.Status.IN_PROGRESS
        progress.save(update_fields=["opened_at", "status"])

    # запрещаем просмотр решённой задачи (по твоему ТЗ)
    if progress.status == StudentTaskProgress.Status.SOLVED and progress.locked_after_solve:
        return JsonResponse(
            {"ok": True, "locked": True, "message": "Задача уже решена и недоступна для просмотра"},
            status=200
        )

    visible_tests = list(
        TaskTestCase.objects.filter(task=task, is_visible=True)
        .order_by("ordinal")
        .values("ordinal", "stdin", "expected_stdout")
    )

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
    })




@csrf_exempt  # на MVP; позже можно убрать и настроить CSRF нормально
@require_POST
def student_submit(request: HttpRequest, task_id: int):
    """
    POST /api/student/task/<task_id>/submit
    body: {"code": "..."}
    Реальная проверка через Judge0 (RapidAPI) батчем:
      - 1 POST /submissions/batch
      - несколько GET /submissions/batch (poll)
    Оптимизации:
      - cooldown 15 секунд между сабмитами
      - запрет сабмита, если код не изменился с прошлого сабмита
    """

    # 0) auth
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    # 1) active session check
    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse({"ok": True, "active": False, "message": "Текущая сессия неактивна"}, status=200)

    task = get_object_or_404(SessionTask, id=task_id, session=session)

    # 2) input
    data = _json_body(request)
    code = (data.get("code") or "").rstrip()
    if not code:
        return JsonResponse({"ok": False, "error": "code is required"}, status=400)

    now = timezone.now()

    # 3) StudentSession
    ss, _ = StudentSession.objects.get_or_create(
        student_id=student_id,
        session=session,
        defaults={"started_at": now, "last_seen_at": now},
    )
    StudentSession.objects.filter(id=ss.id).update(last_seen_at=now)

    # 4) Progress
    progress, _ = StudentTaskProgress.objects.get_or_create(
        student_session=ss,
        task=task,
        defaults={"status": StudentTaskProgress.Status.IN_PROGRESS, "opened_at": now},
    )

    # если уже решено и locked — не принимаем
    if progress.status == StudentTaskProgress.Status.SOLVED and progress.locked_after_solve:
        return JsonResponse({"ok": True, "locked": True, "message": "Задача уже решена"}, status=200)

    # 5) Anti-spam: cooldown 15 sec
    # Требует поля progress.last_submit_at (DateTimeField null=True blank=True)
    if getattr(progress, "last_submit_at", None):
        delta = (now - progress.last_submit_at).total_seconds()
        if delta < 15:
            return JsonResponse(
                {"ok": False, "error": f"Too frequent submits. Wait {int(15 - delta)}s"},
                status=429
            )

    # 6) Anti-spam: no code change
    # Требует поля progress.last_code_hash (CharField max_length=64 default="")
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if getattr(progress, "last_code_hash", "") and progress.last_code_hash == code_hash:
        return JsonResponse(
            {"ok": False, "error": "No changes in code since last submit"},
            status=400
        )

    # 7) Increase attempt counters
    progress.attempts_total += 1
    attempt_no = progress.attempts_total

    # 8) Load testcases
    testcases = list(
        TaskTestCase.objects.filter(task=task).order_by("ordinal")
        .values("ordinal", "stdin", "expected_stdout")
    )
    if not testcases:
        return JsonResponse({"ok": False, "error": "No testcases configured for this task"}, status=500)

    # 9) Judge0 batch run
    try:
        tokens = create_batch_submissions(code, testcases)
        results = wait_batch(tokens, timeout_sec=30, poll_interval=0.9)
    except Exception as e:
        # не считаем попытку "проваленной" из-за внешнего сервиса, но сохраняем сабмишн как runtime_error
        verdict = Submission.Verdict.RUNTIME_ERROR
        stdout_last = ""
        stderr_last = f"Judge0 error: {type(e).__name__}: {e}"
        passed_tests = 0
        total_tests = len(testcases)

        # обновим антиспам-метки, чтобы ученик не мог спамить сразу
        if hasattr(progress, "last_submit_at"):
            progress.last_submit_at = now
        if hasattr(progress, "last_code_hash"):
            progress.last_code_hash = code_hash

        progress.attempts_failed += 1

        if progress.attempts_failed == 5 and not progress.hint1_unlocked_at:
            progress.hint1_unlocked_at = now
        if progress.attempts_failed == 8 and not progress.hint2_unlocked_at:
            progress.hint2_unlocked_at = now

        update_fields = ["attempts_total", "attempts_failed", "hint1_unlocked_at", "hint2_unlocked_at"]
        if hasattr(progress, "last_submit_at"):
            update_fields.append("last_submit_at")
        if hasattr(progress, "last_code_hash"):
            update_fields.append("last_code_hash")

        progress.save(update_fields=update_fields)

        sub = Submission.objects.create(
            progress=progress,
            attempt_no=attempt_no,
            code=code,
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

    # 10) Interpret results
    # Judge0: 3 Accepted, 4 Wrong Answer, 5 TLE, 6 CE, 7+ RE
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

    # 11) update progress anti-spam markers
    if hasattr(progress, "last_submit_at"):
        progress.last_submit_at = now
    if hasattr(progress, "last_code_hash"):
        progress.last_code_hash = code_hash

    # 12) update progress verdict logic
    if verdict == Submission.Verdict.ACCEPTED:
        progress.status = StudentTaskProgress.Status.SOLVED
        progress.solved_at = now
        progress.locked_after_solve = True
    else:
        progress.attempts_failed += 1

        # unlock hints at 5/8 failed attempts
        if progress.attempts_failed == 5 and not progress.hint1_unlocked_at:
            progress.hint1_unlocked_at = now
        if progress.attempts_failed == 8 and not progress.hint2_unlocked_at:
            progress.hint2_unlocked_at = now

    update_fields = [
        "attempts_total",
        "attempts_failed",
        "status",
        "solved_at",
        "locked_after_solve",
        "hint1_unlocked_at",
        "hint2_unlocked_at",
    ]
    if hasattr(progress, "last_submit_at"):
        update_fields.append("last_submit_at")
    if hasattr(progress, "last_code_hash"):
        update_fields.append("last_code_hash")

    progress.save(update_fields=update_fields)

    # 13) store submission
    sub = Submission.objects.create(
        progress=progress,
        attempt_no=attempt_no,
        code=code,
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




# HTML-страница логина ученика
@require_http_methods(["GET", "POST"])
def student_login_page(request: HttpRequest):
    if request.method == "GET":
        return render(request, "core/student_login.html")

    # POST из формы
    full_name = (request.POST.get("full_name") or "").strip()
    pin = (request.POST.get("pin") or "").strip()

    # используем ту же логику, что и API (простая)
    if not full_name or not pin or not pin.isdigit() or len(pin) != 6:
        return render(request, "core/student_login.html", {"error": "Введите имя и PIN (6 цифр)."})

    student = (
        Student.objects.select_related("class_group")
        .filter(full_name__iexact=full_name, is_active=True)
        .first()
    )
    if not student or not student.check_pin(pin):
        return render(request, "core/student_login.html", {"error": "Неверное имя или PIN."})

    request.session["student_id"] = student.id
    request.session["student_name"] = student.full_name
    request.session["student_class_id"] = student.class_group_id
    request.session["student_logged_in_at"] = timezone.now().isoformat()

    return redirect("/student/")


# HTML-страница портала ученика
def student_portal_page(request: HttpRequest):
    if not request.session.get("student_id"):
        return redirect("/student/login/")
    return render(request, "core/student_portal.html")


@require_POST
def student_logout_page(request: HttpRequest):
    request.session.flush()
    return redirect("/student/login/")

def student_hint_level(request: HttpRequest, task_id: int, level: int):
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    if level not in (1, 2):
        return JsonResponse({"ok": False, "error": "invalid level"}, status=400)

    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse(
            {"ok": True, "active": False, "message": "Current session is inactive"},
            status=200
        )

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

    # 1) Check thresholds
    if level == 1 and progress.attempts_failed < 5:
        return JsonResponse({"ok": False, "error": "hint level 1 not available yet"}, status=403)
    if level == 2 and progress.attempts_failed < 8:
        return JsonResponse({"ok": False, "error": "hint level 2 not available yet"}, status=403)

    # 2) Cache in progress
    if level == 1 and progress.hint1_text:
        return JsonResponse({"ok": True, "level": 1, "text": progress.hint1_text})
    if level == 2 and progress.hint2_text:
        return JsonResponse({"ok": True, "level": 2, "text": progress.hint2_text})

    # 3) Cache in AiAssistMessage
    cached = (AiAssistMessage.objects
              .filter(progress=progress, level=level, status=AiAssistMessage.Status.OK)
              .order_by("-created_at")
              .first())
    if cached and cached.response_text:
        if level == 1:
            progress.hint1_text = cached.response_text
            progress.save(update_fields=["hint1_text"])
        else:
            progress.hint2_text = cached.response_text
            progress.save(update_fields=["hint2_text"])
        return JsonResponse({"ok": True, "level": level, "text": cached.response_text})

    # 4) Build context
    visible_tests = list(
        TaskTestCase.objects.filter(task=task, is_visible=True)
        .order_by("ordinal")
        .values("stdin", "expected_stdout")
    )
    last_sub = (Submission.objects
                .filter(progress=progress)
                .order_by("-attempt_no")
                .first())
    last_subs = list(Submission.objects.filter(progress=progress).order_by("attempt_no")[:50])

    prompt_snapshot = build_prompt_snapshot(
        level=level,
        statement=task.statement,
        constraints=task.constraints,
        visible_tests=visible_tests,
        last_submission=last_sub,
        last_submissions=last_subs,
    )

    # 5) Create log row first
    msg = AiAssistMessage.objects.create(
        progress=progress,
        level=level,
        prompt_snapshot=prompt_snapshot,
        status=AiAssistMessage.Status.ERROR,
        error_message="pending",
    )

    # 6) Call OpenAI (new schema: data = {"text": "...", "no_code_confirmed": bool})
    try:
        out = call_openai_hint(level, prompt_snapshot)
        data = out.get("data") or {}

        text = (data.get("text") or "").strip()
        if not text:
            raise ValueError("Empty AI response text")

        # hard safety filter
        text = sanitize_no_code(text)

        # save ai message
        msg.response_text = text
        msg.model = out.get("model", "")
        msg.tokens_in = out.get("tokens_in")
        msg.tokens_out = out.get("tokens_out")
        msg.status = AiAssistMessage.Status.OK
        msg.error_message = ""
        msg.save(update_fields=["response_text", "model", "tokens_in", "tokens_out", "status", "error_message"])

        # cache in progress
        if level == 1:
            progress.hint1_text = text
            progress.save(update_fields=["hint1_text"])
        else:
            progress.hint2_text = text
            progress.save(update_fields=["hint2_text"])

        return JsonResponse({"ok": True, "level": level, "text": text})

    except Exception as e:
        msg.status = AiAssistMessage.Status.ERROR
        msg.error_message = f"{type(e).__name__}: {e}"
        msg.save(update_fields=["status", "error_message"])
        return JsonResponse({"ok": False, "error": "AI assistant temporarily unavailable"}, status=502)
