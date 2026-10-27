import json

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from core.ui_translations import get_ui_lang

from .permissions import tower_student_required
from .services.companion_sources import select_learning_source
from .services.companion import acknowledge_companion, companion_status, request_companion_reply
from .services.companion_delivery import SourceReservationError


@require_GET
@tower_student_required
def learning_source(request):
    source = select_learning_source(student=request.tower_student)
    return JsonResponse({"ok": True, "state": "available" if source else "empty",
                         "source": source.as_dict() if source else None})


@require_GET
@tower_student_required
def status(request):
    surface = request.GET.get("surface", "learning")
    if surface not in ("learning", "game", "exam"):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)
    return JsonResponse({"ok": True, **companion_status(student=request.tower_student, surface=surface)})


@require_POST
@tower_student_required
def action(request, action):
    try:
        if len(request.body) > 2048:
            raise ValueError("invalid_payload")
        values = json.loads(request.body)
        if not isinstance(values, dict):
            raise ValueError("invalid_payload")
        surface = values.pop("surface", "learning")
        if surface not in ("learning", "game", "exam"):
            raise ValueError("invalid_payload")
        if action == "generate" and set(values) == {"request_id"}:
            result = request_companion_reply(student=request.tower_student, request_id=values["request_id"],
                                             language=get_ui_lang(request), surface=surface)
            return JsonResponse({"ok": True, **result})
        if action == "acknowledge" and set(values) == {"message_id"}:
            acknowledge_companion(student=request.tower_student, message_id=values["message_id"], surface=surface)
            return JsonResponse({"ok": True})
        raise ValueError("invalid_payload")
    except PermissionDenied:
        return JsonResponse({"ok": False, "error": "tower_access_denied"}, status=403)
    except (ValueError, UnicodeDecodeError, SourceReservationError):
        return JsonResponse({"ok": False, "error": "invalid_request"}, status=400)
