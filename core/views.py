import json
import re
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

from .models import Student


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
from django.db.models import Prefetch
from .models import Session, SessionTask, StudentSession, StudentTaskProgress


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


from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET
from .models import (
    Session, SessionTask, TaskTestCase,
    StudentSession, StudentTaskProgress, Submission
)


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


@csrf_exempt  # пока MVP; позже уберём и сделаем CSRF нормально
@require_POST
def student_submit(request: HttpRequest, task_id: int):
    """
    POST /api/student/task/<task_id>/submit
    body: {"code": "..."}
    Пока делаем заглушку проверки:
      - сохраняем submission с verdict="wrong_answer"
    Дальше подключим judge (Judge0) и будем реально гонять тесты.
    """
    student_id, class_id = _get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)

    session = _get_active_session_for_class(class_id)
    if not session:
        return JsonResponse({"ok": True, "active": False, "message": "Текущая сессия неактивна"}, status=200)

    task = get_object_or_404(SessionTask, id=task_id, session=session)

    data = _json_body(request)
    code = (data.get("code") or "").rstrip()
    if not code:
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

    # если уже решено и locked — не принимаем
    if progress.status == StudentTaskProgress.Status.SOLVED and progress.locked_after_solve:
        return JsonResponse({"ok": True, "locked": True, "message": "Задача уже решена"}, status=200)

    # увеличиваем счётчики попыток
    progress.attempts_total += 1
    attempt_no = progress.attempts_total

    # ====== MVP заглушка (пока без judge) ======
    verdict = Submission.Verdict.WRONG_ANSWER
    stdout = ""
    stderr = ""
    passed_tests = 0
    total_tests = TaskTestCase.objects.filter(task=task).count()

    if verdict != Submission.Verdict.ACCEPTED:
        progress.attempts_failed += 1

    # Открываем подсказки по порогам 5/8 (без текста, просто “доступно”)
    if progress.attempts_failed == 5 and not progress.hint1_unlocked_at:
        progress.hint1_unlocked_at = now
    if progress.attempts_failed == 8 and not progress.hint2_unlocked_at:
        progress.hint2_unlocked_at = now

    progress.save(update_fields=[
        "attempts_total", "attempts_failed",
        "hint1_unlocked_at", "hint2_unlocked_at",
    ])

    sub = Submission.objects.create(
        progress=progress,
        attempt_no=attempt_no,
        code=code,
        verdict=verdict,
        stdout=stdout,
        stderr=stderr,
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
