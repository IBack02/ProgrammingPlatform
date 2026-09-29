"""Atomic ordering for the teacher's module editor."""
import json

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .models import GameModule, Session, SessionTask, TheoryMaterialModule, TheoryQuizModule
from .views import _get_logged_in_teacher, _teacher_api_unauthorized


MODULE_MODELS = {
    "coding_task": SessionTask,
    "theory_material": TheoryMaterialModule,
    "theory_quiz": TheoryQuizModule,
    "game": GameModule,
}


def _read_order(request, modules=False):
    try:
        data = json.loads(request.body)
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError
        if modules:
            if any(
                not isinstance(item, dict)
                or type(item.get("id")) is not int or item["id"] < 1
                or item.get("module_type") not in MODULE_MODELS
                for item in items
            ):
                raise ValueError
            order = [(item["module_type"], item["id"]) for item in items]
        else:
            if any(type(item) is not int or item < 1 for item in items):
                raise ValueError
            order = items
        if len(order) != len(set(order)):
            raise ValueError
        return order
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def _write_order(rows, field):
    # Vacate all existing positions first: direct swaps violate unique indexes.
    offset = max([len(rows)] + [getattr(row, field) for row in rows]) + 1
    for index, row in enumerate(rows):
        type(row).objects.filter(pk=row.pk).update(**{field: offset + index})
    for index, row in enumerate(rows, start=1):
        type(row).objects.filter(pk=row.pk).update(**{field: index})


def _invalid_order():
    return JsonResponse({"ok": False, "error": "invalid order"}, status=400)


def _changed_list():
    return JsonResponse({"ok": False, "error": "list changed; reload it before reordering"}, status=409)


@require_POST
def teacher_modules_reorder_api(request, session_id):
    teacher = _get_logged_in_teacher(request)
    if not teacher:
        return _teacher_api_unauthorized()
    order = _read_order(request, modules=True)
    if order is None:
        return _invalid_order()
    with transaction.atomic():
        session = get_object_or_404(Session.objects.select_for_update(), pk=session_id, author=teacher)
        rows = {
            (kind, row.pk): row
            for kind, model in MODULE_MODELS.items()
            for row in model.objects.select_for_update().filter(session=session).order_by("pk")
        }
        if set(order) != set(rows):
            return _changed_list()
        _write_order([rows[key] for key in order], "position")
    return JsonResponse({"ok": True})


def _reorder_children(request, module_id, model, relation):
    teacher = _get_logged_in_teacher(request)
    if not teacher:
        return _teacher_api_unauthorized()
    order = _read_order(request)
    if order is None:
        return _invalid_order()
    with transaction.atomic():
        module = get_object_or_404(
            model.objects.select_for_update(), pk=module_id, session__author=teacher,
        )
        rows = {row.pk: row for row in getattr(module, relation).select_for_update().order_by("pk")}
        if set(order) != set(rows):
            return _changed_list()
        _write_order([rows[key] for key in order], "ordinal")
    return JsonResponse({"ok": True})


@require_POST
def teacher_theory_blocks_reorder_api(request, module_id):
    return _reorder_children(request, module_id, TheoryMaterialModule, "blocks")


@require_POST
def teacher_quiz_questions_reorder_api(request, module_id):
    return _reorder_children(request, module_id, TheoryQuizModule, "questions")
