import json
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from core.ui_translations import UI_TRANSLATIONS, get_ui_lang

from .permissions import managed_classes, tower_student_required, tower_teacher_required
from .collection_security import collection_unlocked_required
from .catalog import presentation_catalog
from .wallet_views import student_wallet_context, wallet_errors
from .services.audit import record_access_change


logger = logging.getLogger(__name__)


def _serialize_class(group):
    return {
        "id": group.pk,
        "name": group.name,
        "tower_enabled": group.tower_enabled,
        "is_guest": group.is_guest,
        "can_enable": bool(not group.is_guest and group.owner and group.owner.is_active and group.owner.can_manage_tower),
    }


def _set_class_access(request, class_id, enabled):
    with transaction.atomic():
        # Lock only the class row: owner is nullable, which PostgreSQL cannot
        # lock through a nullable outer join from select_related("owner").
        groups = managed_classes(request).select_related(None).select_for_update()
        group = get_object_or_404(groups, pk=class_id)
        if enabled and not _serialize_class(group)["can_enable"]:
            raise ValidationError(UI_TRANSLATIONS[get_ui_lang(request)]["tower_class_ineligible"])
        if group.tower_enabled != enabled:
            before = group.tower_enabled
            group.tower_enabled = enabled
            group.save(update_fields=["tower_enabled"])
            record_access_change(request, event_type="class_access", group=group, before=before, after=enabled)
            logger.info(
                "Tower class access changed: class=%s enabled=%s teacher=%s admin=%s",
                group.pk, enabled, getattr(request.tower_teacher, "pk", None),
                request.user.pk if request.user.is_authenticated else None,
            )
        return _serialize_class(group)


@require_GET
@tower_student_required
@collection_unlocked_required
@wallet_errors
def home(request):
    return render(request, "tower/home.html", student_wallet_context(request))


@require_GET
@tower_student_required
def status(request):
    return JsonResponse({
        "ok": True,
        "student_id": request.tower_student.pk,
        "classes": [{"id": group.pk, "name": group.name} for group in request.tower_classes],
    })


@require_http_methods(["GET", "POST"])
@tower_teacher_required
@ensure_csrf_cookie
def teacher_page(request):
    if request.method == "GET":
        from .wallet_views import teacher_wallets
        return teacher_wallets(request)
    error = ""
    if request.method == "POST":
        try:
            class_id = int(request.POST.get("class_id", ""))
            if class_id <= 0 or request.POST.get("tower_enabled", "") not in ("", "on"):
                raise ValueError
        except (TypeError, ValueError):
            return render(request, "tower/denied.html", status=400)
        try:
            _set_class_access(request, class_id, request.POST.get("tower_enabled") == "on")
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            return redirect("tower:teacher")
    return render(request, "tower/teacher.html", {
        "active": "tower",
        "tower_classes": [_serialize_class(group) for group in managed_classes(request)],
        "tower_error": error,
    }, status=400 if error else 200)


@require_GET
@tower_teacher_required
def preview(request):
    return render(request, "tower/home.html", {
        "tower_preview": True, **presentation_catalog(get_ui_lang(request)),
    })


@require_GET
@tower_teacher_required
def teacher_classes(request):
    return JsonResponse({"ok": True, "classes": [_serialize_class(group) for group in managed_classes(request)]})


@require_http_methods(["PATCH"])
@tower_teacher_required
def class_access(request, class_id):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid JSON"}, status=400)
    if not isinstance(data, dict) or set(data) != {"tower_enabled"} or type(data["tower_enabled"]) is not bool:
        return JsonResponse({"ok": False, "error": "tower_enabled must be a boolean"}, status=400)
    try:
        group = _set_class_access(request, class_id, data["tower_enabled"])
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": exc.messages[0]}, status=400)
    return JsonResponse({"ok": True, "class": group})
