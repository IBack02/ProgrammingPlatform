"""Teacher lesson presentation, practice quizzes and class-scoped annotations."""
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from . import views
from .models import LessonHighlight, TheoryMaterialModule

MARKER_COLORS = {"yellow", "green", "blue", "pink", "purple", "orange", "red"}


@views.teacher_required
@ensure_csrf_cookie
def teacher_lesson_page(request, session_id):
    teacher = views._get_logged_in_teacher(request)
    session = views._require_session_owner_or_404(session_id, teacher)
    return render(request, "core/teacher/lesson.html", {
        "active": "sessions", "lesson_session": session,
        "lesson_classes": session.allowed_classes.filter(owner=teacher).order_by("name"),
    })


@require_http_methods(["GET", "PUT"])
def teacher_highlights(request, module_id):
    teacher = views._get_logged_in_teacher(request)
    if not teacher:
        return views._teacher_api_unauthorized()
    module = views._require_theory_module_owner_or_404(module_id, teacher)
    class_id = request.GET.get("class_id", "")
    if not class_id.isdigit():
        return JsonResponse({"ok": False, "error": "Select a class"}, status=400)
    group = get_object_or_404(module.session.allowed_classes, id=int(class_id), owner=teacher)
    if request.method == "PUT":
        ranges = views._json_body(request).get("ranges")
        if not isinstance(ranges, list) or len(ranges) > 500:
            return JsonResponse({"ok": False, "error": "Invalid highlights"}, status=400)
        blocks = {b.id: b for b in module.blocks.all()}
        clean = []
        for row in ranges:
            if not isinstance(row, dict):
                return JsonResponse({"ok": False, "error": "Invalid highlight"}, status=400)
            block = blocks.get(row.get("block_id")) if isinstance(row.get("block_id"), int) else None
            if block and "text" in row and row["text"] != block.content:
                return JsonResponse({"ok": False, "error": "Text has changed; reload the module"}, status=409)
            start, end = row.get("start"), row.get("end")
            color = row.get("color", "yellow")
            if not isinstance(color, str) or color not in MARKER_COLORS:
                return JsonResponse({"ok": False, "error": "Invalid marker color"}, status=400)
            if (not block or block.block_type not in ("heading", "text", "code")
                    or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(block.content.encode("utf-16-le")) // 2):
                return JsonResponse({"ok": False, "error": "Invalid highlight range"}, status=400)
            # Bind offsets to the displayed content; stale marks disappear after editing a block.
            clean.append({"block_id": block.id, "start": start, "end": end, "text": block.content, "color": color})
        state, _ = LessonHighlight.objects.update_or_create(module=module, class_group=group, defaults={"ranges": clean})
    else:
        state = LessonHighlight.objects.filter(module=module, class_group=group).first()
    return JsonResponse({"ok": True, "ranges": state.ranges if state else []})


@require_GET
def student_highlights(request, module_id):
    student_id, class_id = views._get_student_from_session(request)
    if not student_id:
        return JsonResponse({"ok": False, "error": "not authenticated"}, status=401)
    module = get_object_or_404(TheoryMaterialModule.objects.select_related("session"), id=module_id, is_active=True)
    if not views._student_can_access_session(module.session, class_id=class_id, student_id=student_id):
        return JsonResponse({"ok": False, "error": "session is not accessible"}, status=403)
    active_class_id = views._student_session_class_id(module.session, student_id=student_id, preferred_class_id=class_id)
    state = LessonHighlight.objects.filter(module=module, class_group_id=active_class_id).first()
    return JsonResponse({"ok": True, "ranges": state.ranges if state else []})


@require_http_methods(["GET", "POST"])
def teacher_practice_quiz(request, module_id):
    teacher = views._get_logged_in_teacher(request)
    if not teacher:
        return views._teacher_api_unauthorized()
    module = views._require_theory_quiz_owner_or_404(module_id, teacher)
    participant = f"teacher:{teacher.id}"
    if request.method == "GET":
        return JsonResponse({"ok": True, "module": {
            "id": module.id, "title": module.title, "topic": module.topic,
            "instructions": module.instructions,
            "questions": views._public_quiz_questions(module, participant),
        }})
    if views.request_is_limited("teacher_practice_quiz", str(teacher.id), limit=30, window_seconds=3600):
        return JsonResponse({"ok": False, "error": "Quiz submission limit exceeded"}, status=429)
    answers = views._json_body(request).get("answers", {})
    if not isinstance(answers, dict):
        return JsonResponse({"ok": False, "error": "answers must be an object"}, status=400)
    results, correct, total, score = views._grade_theory_quiz(module, answers, participant)
    return JsonResponse({"ok": True, "attempt": {
        "results": results, "correct_answers": correct, "total_questions": total, "score_percent": score,
    }})
