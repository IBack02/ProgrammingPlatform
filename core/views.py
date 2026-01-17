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