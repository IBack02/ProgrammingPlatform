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
