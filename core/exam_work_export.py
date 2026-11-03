"""Owner-only downloads of individual exam work; peer assessment is never queried."""
import re
from decimal import Decimal

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .exam_views import _expire_if_needed, _https_url, _teacher, _teacher_required
from .models import ExamAnswer, ExamAttempt
from .ui_translations import get_ui_lang


def _safe_url(value):
    try:
        return _https_url(value, "image_url")
    except ValueError:
        return ""


def collect_exam_work(attempt, request):
    questions = list(attempt.exam.questions.prefetch_related("matching_pairs").order_by("position", "id"))
    answers = {answer.question_id: answer for answer in attempt.answers.all()}
    rows = []
    for question in questions:
        answer = answers.get(question.pk)
        schema = question.table_schema or {}
        columns = [str(column.get("label", "")) for column in schema.get("columns", [])]
        cells = answer.table_answer if answer else {}
        table_rows, expected_rows = [], []
        for schema_row in schema.get("rows", []):
            table_rows.append([str(cell.get("value", "") if cell.get("mode") == "given" else cells.get(str(cell.get("key")), ""))
                               for cell in schema_row.get("cells", [])])
            expected_rows.append([str(cell.get("value", "") if cell.get("mode") == "given" else cell.get("answer", ""))
                                  for cell in schema_row.get("cells", [])])
        presentation = attempt.presentation_json.get(str(question.pk), {})
        right = {str(item["key"]): str(item["text"]) for item in presentation.get("right", [])}
        selected = answer.matching_answer if answer else {}
        pairs = list(question.matching_pairs.all())
        left = presentation.get("left", []) or [{"key": str(pair.pk), "text": pair.left_text} for pair in pairs]
        matching_rows = [[str(item["text"]), right.get(str(selected.get(str(item["key"]))), "")] for item in left]
        expected_text = question.model_answer
        if question.question_type == "matching":
            expected_text = "\n".join(filter(None, [question.model_answer, *[f"{pair.left_text} → {pair.right_text}" for pair in pairs]]))
        choices = question.choices or []
        if question.question_type == "single_choice":
            expected_text = "\n".join(str(choice["text"]) for choice in choices if choice.get("is_correct")) or question.model_answer
        answer_text = answer.text_answer if answer else ""
        if answer and question.question_type == "single_choice" and answer.choice_answer:
            answer_text = next((str(choice["text"]) for choice in choices if str(choice["key"]) == answer.choice_answer), answer_text)
        diagram_url = _safe_url(answer.diagram_file_url) if answer else ""
        if answer and answer.diagram_xml and not diagram_url:
            diagram_url = request.build_absolute_uri(reverse("teacher_exam_answer_diagram_download", args=[answer.pk]))
        rows.append({"position": question.position, "type": question.question_type, "prompt": question.prompt,
                     "maximum": question.max_score, "score": answer.awarded_score if answer else None,
                     "feedback": answer.teacher_feedback if answer else "", "answer_text": answer_text,
                     "image_url": _safe_url(question.image_url), "diagram_url": diagram_url,
                     "choices": [{"key": str(c["key"]), "text": str(c["text"])} for c in choices],
                     "matching_rows": matching_rows, "matching_options": list(right.values()),
                     "table_columns": columns, "table_rows": table_rows, "expected_table_rows": expected_rows,
                     "expected_text": expected_text})
    scores = [row["score"] for row in rows if row["score"] is not None]
    return {"exam_title": attempt.exam.title, "topic": attempt.exam.topic, "instructions": attempt.exam.instructions,
            "student_name": attempt.student.full_name, "class_name": attempt.student.class_group.name,
            "submitted_at": timezone.localtime(attempt.submitted_at).strftime("%d.%m.%Y %H:%M %z") if attempt.submitted_at else "",
            "teacher_score": sum(scores, Decimal("0")) if scores else None,
            "maximum": sum((row["maximum"] for row in rows), Decimal("0")), "graded_count": len(scores), "questions": rows}


def _download(content, content_type, filename):
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(True, filename)
    return response


@_teacher_required
@require_GET
@never_cache
def teacher_exam_attempt_download(request, attempt_id):
    attempt = get_object_or_404(ExamAttempt.objects.select_related("exam", "student__class_group"),
                                pk=attempt_id, exam__owner=_teacher(request))
    _expire_if_needed(attempt)
    from .exam_work_docx import render_exam_work
    data = collect_exam_work(attempt, request)
    student = re.sub(r"[^\w.-]+", "_", attempt.student.full_name).strip("._")[:80] or str(attempt.student_id)
    return _download(render_exam_work(data, get_ui_lang(request)),
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     f"exam-{attempt.exam_id}-{student}-{attempt.pk}.docx")


@_teacher_required
@require_GET
@never_cache
def teacher_exam_answer_diagram_download(request, answer_id):
    answer = get_object_or_404(ExamAnswer.objects.select_related("attempt", "question"),
                               pk=answer_id, attempt__exam__owner=_teacher(request), question__question_type="diagram")
    if not answer.diagram_xml:
        from django.http import Http404
        raise Http404
    return _download(answer.diagram_xml.encode("utf-8"), "application/vnd.jgraph.mxfile",
                     f"exam-{answer.attempt.exam_id}-question-{answer.question.position}.drawio")
