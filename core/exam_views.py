import hmac
import json
import random
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .google_drive import upload_exam_diagram
from .security import auth_version, request_is_limited
from .models import (
    ClassGroup,
    Exam,
    ExamAnswer,
    ExamAttempt,
    ExamClass,
    ExamIntegrityEvent,
    ExamMatchPair,
    ExamQuestion,
    Student,
    Teacher,
)

MAX_JSON_BODY_BYTES = 1_500_000
MAX_DIAGRAM_XML_BYTES = 2_000_000
DRIVE_FILE_RE = re.compile(r"/file/d/([A-Za-z0-9_-]+)")


def _json_body(request: HttpRequest) -> dict:
    if len(request.body) > MAX_JSON_BODY_BYTES:
        raise ValueError("request body is too large")
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON object is required")
    return data


def _teacher(request: HttpRequest):
    teacher_id = request.session.get("teacher_id")
    if not teacher_id:
        return None
    teacher = Teacher.objects.filter(id=teacher_id, is_active=True).first()
    version = request.session.get("teacher_auth_version")
    if not teacher or not version or not hmac.compare_digest(
        version,
        auth_version(teacher.pin_hash),
    ):
        return None
    return teacher


def _student(request: HttpRequest):
    student_id = request.session.get("student_id")
    class_id = request.session.get("student_class_id")
    if not student_id or not class_id:
        return None
    student = Student.objects.select_related("class_group").filter(
        id=student_id,
        class_group_id=class_id,
        is_active=True,
    ).first()
    version = request.session.get("student_auth_version")
    if not student or not version or not hmac.compare_digest(
        version,
        auth_version(student.pin_hash),
    ):
        return None
    return student


def _teacher_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not _teacher(request):
            return redirect("/teacher/login/")
        return view_func(request, *args, **kwargs)
    return wrapped


def _student_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not _student(request):
            return redirect("/student/login/")
        return view_func(request, *args, **kwargs)
    return wrapped


def _api_error(message: str, status: int = 400):
    return JsonResponse({"ok": False, "error": message}, status=status)




def _json_errors(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except Http404:
            return _api_error("resource not found", 404)
        except IntegrityError:
            return _api_error("database conflict", 409)
        except Exception as exc:
            message = str(exc) if settings.DEBUG else "internal server error"
            return _api_error(message, 500)
    return wrapped

def _https_url(value, field_name: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    has_unsafe_chars = any(ord(char) < 32 or char in '"<>\\' for char in value)
    if has_unsafe_chars or parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{field_name} must be a valid HTTPS URL")
    return value


def _student_image_url(value: str) -> str:
    match = DRIVE_FILE_RE.search(value or "")
    if match:
        return f"https://drive.google.com/thumbnail?id={match.group(1)}&sz=w1800"
    return value


def _positive_int(value, field_name: str, maximum: int = 10000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")
    return parsed


def _score(value) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("max_score must be a number") from exc
    if parsed <= 0 or parsed > Decimal("10000"):
        raise ValueError("max_score must be between 0 and 10000")
    return parsed.quantize(Decimal("0.01"))


def _class_ids_for_teacher(teacher: Teacher, raw_ids) -> list[int]:
    if raw_ids is None:
        return []
    if not isinstance(raw_ids, list):
        raise ValueError("class_ids must be an array")
    try:
        requested = sorted({int(value) for value in raw_ids})
    except (TypeError, ValueError) as exc:
        raise ValueError("class_ids must contain integers") from exc
    owned = set(ClassGroup.objects.filter(owner=teacher, id__in=requested).values_list("id", flat=True))
    if owned != set(requested):
        raise ValueError("one or more classes are unavailable")
    return requested


def _serialize_pair(pair: ExamMatchPair) -> dict:
    return {
        "id": pair.id,
        "position": pair.position,
        "left_text": pair.left_text,
        "right_text": pair.right_text,
    }


def _serialize_question_teacher(question: ExamQuestion) -> dict:
    return {
        "id": question.id,
        "position": question.position,
        "question_type": question.question_type,
        "prompt": question.prompt,
        "image_url": question.image_url,
        "model_answer": question.model_answer,
        "table_schema": question.table_schema,
        "max_score": float(question.max_score),
        "pairs": [_serialize_pair(pair) for pair in question.matching_pairs.all()],
    }


def _serialize_exam(exam: Exam, include_questions: bool = False) -> dict:
    data = {
        "id": exam.id,
        "title": exam.title,
        "topic": exam.topic,
        "instructions": exam.instructions,
        "duration_minutes": exam.duration_minutes,
        "status": exam.status,
        "class_ids": list(exam.allowed_classes.order_by("name").values_list("id", flat=True)),
        "question_count": exam.questions.count(),
        "attempt_count": exam.attempts.count(),
        "created_at": exam.created_at.isoformat(),
        "updated_at": exam.updated_at.isoformat(),
    }
    if include_questions:
        data["questions"] = [
            _serialize_question_teacher(question)
            for question in exam.questions.prefetch_related("matching_pairs").order_by("position", "id")
        ]
        data["next_question_position"] = max(
            (row["position"] for row in data["questions"]), default=0
        ) + 1
    return data


def _parse_pairs(raw_pairs) -> list[dict]:
    if not isinstance(raw_pairs, list) or len(raw_pairs) < 2:
        raise ValueError("matching question requires at least two pairs")
    pairs = []
    for index, raw in enumerate(raw_pairs, start=1):
        if not isinstance(raw, dict):
            raise ValueError("each matching pair must be an object")
        left = str(raw.get("left_text") or "").strip()
        right = str(raw.get("right_text") or "").strip()
        if not left or not right:
            raise ValueError("both sides of every matching pair are required")
        pairs.append({"position": index, "left_text": left, "right_text": right})
    return pairs


def _parse_table_schema(raw_schema) -> dict:
    if not isinstance(raw_schema, dict):
        raise ValueError("table_schema must be an object")

    raw_columns = raw_schema.get("columns")
    raw_rows = raw_schema.get("rows")
    if not isinstance(raw_columns, list) or not 2 <= len(raw_columns) <= 8:
        raise ValueError("table must contain between 2 and 8 columns")
    if not isinstance(raw_rows, list) or not 1 <= len(raw_rows) <= 30:
        raise ValueError("table must contain between 1 and 30 rows")

    columns = []
    for index, raw_column in enumerate(raw_columns, start=1):
        label = str(
            raw_column.get("label") if isinstance(raw_column, dict) else raw_column
        ).strip()
        if not label or len(label) > 120:
            raise ValueError("every table column needs a label up to 120 characters")
        columns.append({"key": f"c{index}", "label": label})

    rows = []
    input_count = 0
    for row_index, raw_row in enumerate(raw_rows, start=1):
        raw_cells = raw_row.get("cells") if isinstance(raw_row, dict) else raw_row
        if not isinstance(raw_cells, list) or len(raw_cells) != len(columns):
            raise ValueError("every table row must contain one cell per column")
        cells = []
        for column_index, raw_cell in enumerate(raw_cells, start=1):
            if not isinstance(raw_cell, dict):
                raise ValueError("every table cell must be an object")
            mode = str(raw_cell.get("mode") or "given").strip()
            if mode not in {"given", "input"}:
                raise ValueError("table cell mode must be given or input")
            cell = {
                "key": f"r{row_index}c{column_index}",
                "column_key": f"c{column_index}",
                "mode": mode,
            }
            if mode == "given":
                value = str(raw_cell.get("value") or "")
                if len(value) > 1000:
                    raise ValueError("table cell value is too long")
                cell["value"] = value
            else:
                expected = str(raw_cell.get("answer") or "").strip()
                if not expected or len(expected) > 1000:
                    raise ValueError("every student table cell needs an expected answer")
                cell["answer"] = expected
                input_count += 1
            cells.append(cell)
        rows.append({"key": f"r{row_index}", "cells": cells})

    if input_count == 0:
        raise ValueError("table must contain at least one student input cell")
    return {"columns": columns, "rows": rows}


def _table_model_answer(schema: dict) -> str:
    answers = {
        cell["key"]: cell["answer"]
        for row in schema.get("rows", [])
        for cell in row.get("cells", [])
        if cell.get("mode") == "input"
    }
    return json.dumps(answers, ensure_ascii=False, sort_keys=True)


def _student_table_schema(schema: dict) -> dict:
    return {
        "columns": [
            {"key": column["key"], "label": column["label"]}
            for column in schema.get("columns", [])
        ],
        "rows": [
            {
                "key": row["key"],
                "cells": [
                    {
                        "key": cell["key"],
                        "column_key": cell["column_key"],
                        "mode": cell["mode"],
                        **({"value": cell.get("value", "")} if cell["mode"] == "given" else {}),
                    }
                    for cell in row.get("cells", [])
                ],
            }
            for row in schema.get("rows", [])
        ],
    }


def _validate_table_answer(question: ExamQuestion, value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("table_answer must be an object")
    allowed = {
        cell["key"]
        for row in question.table_schema.get("rows", [])
        for cell in row.get("cells", [])
        if cell.get("mode") == "input"
    }
    clean = {}
    for key, raw_value in value.items():
        if key not in allowed:
            raise ValueError("table_answer contains an unknown cell")
        text = str(raw_value)
        if len(text) > 1000:
            raise ValueError("table answer cell is too long")
        clean[str(key)] = text
    return clean

def _parse_question_payload(data: dict, defaults: ExamQuestion | None = None) -> dict:
    question_type = str(
        data.get("question_type", defaults.question_type if defaults else "")
    ).strip()
    if question_type not in ExamQuestion.QuestionType.values:
        raise ValueError("unsupported question_type")
    position = _positive_int(
        data.get("position", defaults.position if defaults else None),
        "position",
    )
    prompt = str(data.get("prompt", defaults.prompt if defaults else "")).strip()
    if not prompt:
        raise ValueError("prompt is required")
    if question_type == ExamQuestion.QuestionType.TABLE:
        raw_schema = data.get(
            "table_schema",
            defaults.table_schema if defaults else None,
        )
        table_schema = _parse_table_schema(raw_schema)
        model_answer = _table_model_answer(table_schema)
    else:
        table_schema = {}
        model_answer = str(
            data.get("model_answer", defaults.model_answer if defaults else "")
        ).strip()
        if not model_answer:
            raise ValueError("model_answer is required")
    image_url = _https_url(
        data.get("image_url", defaults.image_url if defaults else ""),
        "image_url",
    )
    max_score = _score(data.get("max_score", defaults.max_score if defaults else 1))
    if question_type == ExamQuestion.QuestionType.MATCHING:
        if "pairs" in data:
            pairs = _parse_pairs(data["pairs"])
        elif defaults:
            pairs = [
                {"position": pair.position, "left_text": pair.left_text, "right_text": pair.right_text}
                for pair in defaults.matching_pairs.order_by("position", "id")
            ]
        else:
            raise ValueError("pairs are required")
    else:
        pairs = []
    return {
        "question_type": question_type,
        "position": position,
        "prompt": prompt,
        "model_answer": model_answer,
        "table_schema": table_schema,
        "image_url": image_url,
        "max_score": max_score,
        "pairs": pairs,
    }


def _create_question(exam: Exam, data: dict) -> ExamQuestion:
    parsed = _parse_question_payload(data)
    if exam.questions.filter(position=parsed["position"]).exists():
        raise ValueError("question position is already used")
    pairs = parsed.pop("pairs")
    question = ExamQuestion.objects.create(exam=exam, **parsed)
    ExamMatchPair.objects.bulk_create(
        [ExamMatchPair(question=question, **pair) for pair in pairs]
    )
    return question


def _update_question(question: ExamQuestion, data: dict) -> ExamQuestion:
    parsed = _parse_question_payload(data, defaults=question)
    if question.exam.questions.filter(position=parsed["position"]).exclude(id=question.id).exists():
        raise ValueError("question position is already used")
    pairs = parsed.pop("pairs")
    for key, value in parsed.items():
        setattr(question, key, value)
    question.save()
    question.matching_pairs.all().delete()
    ExamMatchPair.objects.bulk_create(
        [ExamMatchPair(question=question, **pair) for pair in pairs]
    )
    return question


def _ensure_exam_editable(exam: Exam):
    if exam.attempts.exists():
        raise ValueError("exam content is locked because a student has already started it")


def _set_exam_classes(exam: Exam, class_ids: list[int]):
    ExamClass.objects.filter(exam=exam).delete()
    ExamClass.objects.bulk_create(
        [ExamClass(exam=exam, class_group_id=class_id) for class_id in class_ids]
    )


def _presentation_for_exam(exam: Exam) -> dict:
    presentation = {}
    for question in exam.questions.prefetch_related("matching_pairs"):
        if question.question_type != ExamQuestion.QuestionType.MATCHING:
            continue
        left, right, correct = [], [], {}
        for pair in question.matching_pairs.order_by("position", "id"):
            left_key = secrets.token_urlsafe(12)
            right_key = secrets.token_urlsafe(12)
            left.append({"key": left_key, "text": pair.left_text})
            right.append({"key": right_key, "text": pair.right_text})
            correct[left_key] = right_key
        random.SystemRandom().shuffle(right)
        presentation[str(question.id)] = {
            "left": left,
            "right": right,
            "correct": correct,
        }
    return presentation


def _expire_if_needed(attempt: ExamAttempt) -> bool:
    if attempt.status == ExamAttempt.Status.IN_PROGRESS and timezone.now() >= attempt.expires_at:
        attempt.status = ExamAttempt.Status.EXPIRED
        attempt.submitted_at = attempt.expires_at
        attempt.save(update_fields=["status", "submitted_at", "updated_at"])
        return True
    return attempt.status == ExamAttempt.Status.EXPIRED


def _attempt_summary(attempt: ExamAttempt) -> dict:
    integrity_count = getattr(attempt, "integrity_event_count", None)
    if integrity_count is None:
        integrity_count = attempt.integrity_events.count()
    last_integrity_at = getattr(attempt, "last_integrity_at", None)
    if last_integrity_at is None and integrity_count:
        last_integrity_at = attempt.integrity_events.values_list(
            "created_at", flat=True
        ).first()
    return {
        "id": attempt.id,
        "exam_id": attempt.exam_id,
        "student_id": attempt.student_id,
        "student_name": attempt.student.full_name,
        "class_name": attempt.student.class_group.name,
        "status": attempt.status,
        "started_at": attempt.started_at.isoformat(),
        "expires_at": attempt.expires_at.isoformat(),
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        "total_score": float(attempt.total_score),
        "integrity_event_count": integrity_count,
        "last_integrity_at": last_integrity_at.isoformat() if last_integrity_at else None,
    }


def _serialize_integrity_event(event: ExamIntegrityEvent) -> dict:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "detail": event.detail,
        "client_at": event.client_at.isoformat() if event.client_at else None,
        "created_at": event.created_at.isoformat(),
    }


def _answer_student(answer: ExamAnswer | None) -> dict:
    if not answer:
        return {
            "text_answer": "",
            "matching_answer": {},
            "diagram_xml": "",
            "diagram_file_url": "",
            "table_answer": {},
        }
    return {
        "text_answer": answer.text_answer,
        "matching_answer": answer.matching_answer,
        "diagram_xml": answer.diagram_xml,
        "diagram_file_url": answer.diagram_file_url,
        "table_answer": answer.table_answer,
        "updated_at": answer.updated_at.isoformat(),
    }


def _student_question(question: ExamQuestion, attempt: ExamAttempt, answer: ExamAnswer | None) -> dict:
    data = {
        "id": question.id,
        "position": question.position,
        "question_type": question.question_type,
        "prompt": question.prompt,
        "image_url": _student_image_url(question.image_url),
        "max_score": float(question.max_score),
        "answer": _answer_student(answer),
    }
    if question.question_type == ExamQuestion.QuestionType.MATCHING:
        presentation = attempt.presentation_json.get(str(question.id), {})
        data["left_items"] = presentation.get("left", [])
        data["right_items"] = presentation.get("right", [])
    elif question.question_type == ExamQuestion.QuestionType.TABLE:
        data["table_schema"] = _student_table_schema(question.table_schema)
    return data


def _validate_matching_answer(attempt: ExamAttempt, question: ExamQuestion, value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("matching_answer must be an object")
    presentation = attempt.presentation_json.get(str(question.id), {})
    left_keys = {item["key"] for item in presentation.get("left", [])}
    right_keys = {item["key"] for item in presentation.get("right", [])}
    clean = {}
    for left_key, right_key in value.items():
        if left_key not in left_keys or right_key not in right_keys:
            raise ValueError("matching_answer contains an unknown item")
        clean[str(left_key)] = str(right_key)
    return clean


def _save_answer(attempt: ExamAttempt, question: ExamQuestion, data: dict) -> tuple[ExamAnswer, str]:
    answer, _ = ExamAnswer.objects.get_or_create(attempt=attempt, question=question)
    warning = ""
    if question.question_type == ExamQuestion.QuestionType.OPEN_TEXT:
        answer.text_answer = str(data.get("text_answer") or "")[:50000]
    elif question.question_type == ExamQuestion.QuestionType.MATCHING:
        answer.matching_answer = _validate_matching_answer(
            attempt, question, data.get("matching_answer") or {}
        )
    elif question.question_type == ExamQuestion.QuestionType.TABLE:
        answer.table_answer = _validate_table_answer(
            question, data.get("table_answer") or {}
        )
    elif question.question_type == ExamQuestion.QuestionType.DIAGRAM:
        xml = str(data.get("diagram_xml") or "")
        if len(xml.encode("utf-8")) > MAX_DIAGRAM_XML_BYTES:
            raise ValueError("diagram is too large")
        answer.diagram_xml = xml
        if xml and data.get("upload_to_drive", True):
            safe_student = re.sub(r"[^A-Za-z0-9_-]+", "_", attempt.student.full_name)[:50]
            filename = f"exam-{attempt.exam_id}-student-{safe_student}-question-{question.position}.drawio"
            try:
                drive_url = upload_exam_diagram(xml, filename, answer.diagram_file_url)
                if drive_url:
                    answer.diagram_file_url = drive_url
                else:
                    warning = "Google Drive is not configured; diagram XML was saved in the database"
            except Exception:
                warning = "Google Drive upload failed; diagram XML was saved in the database"
    answer.save()
    return answer, warning


@_teacher_required
@ensure_csrf_cookie
def teacher_exams_page(request: HttpRequest):
    return render(request, "core/teacher/exams.html", {"active": "exams"})


@_student_required
@ensure_csrf_cookie
def student_exams_page(request: HttpRequest):
    return render(request, "core/student_exams.html")


@_json_errors
@require_http_methods(["GET", "POST"])
def teacher_exams_api(request: HttpRequest):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    try:
        if request.method == "GET":
            exams = Exam.objects.filter(owner=teacher).prefetch_related("allowed_classes")
            return JsonResponse({"ok": True, "exams": [_serialize_exam(exam) for exam in exams]})
        data = _json_body(request)
        title = str(data.get("title") or "").strip()
        if not title:
            return _api_error("title is required")
        duration = _positive_int(data.get("duration_minutes", 60), "duration_minutes", 1440)
        class_ids = _class_ids_for_teacher(teacher, data.get("class_ids", []))
        with transaction.atomic():
            exam = Exam.objects.create(
                owner=teacher,
                title=title,
                topic=str(data.get("topic") or "").strip(),
                instructions=str(data.get("instructions") or "").strip(),
                duration_minutes=duration,
            )
            _set_exam_classes(exam, class_ids)
        return JsonResponse({"ok": True, "exam": _serialize_exam(exam, True)}, status=201)
    except (ValueError, IntegrityError) as exc:
        return _api_error(str(exc))


@_json_errors
@require_http_methods(["GET", "PATCH", "DELETE"])
def teacher_exam_detail_api(request: HttpRequest, exam_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    exam = get_object_or_404(Exam, id=exam_id, owner=teacher)
    try:
        if request.method == "GET":
            return JsonResponse({"ok": True, "exam": _serialize_exam(exam, True)})
        if request.method == "DELETE":
            _ensure_exam_editable(exam)
            exam.delete()
            return JsonResponse({"ok": True})
        data = _json_body(request)
        if {"title", "topic", "instructions", "duration_minutes", "class_ids"}.intersection(data):
            _ensure_exam_editable(exam)
        with transaction.atomic():
            if "title" in data:
                title = str(data.get("title") or "").strip()
                if not title:
                    raise ValueError("title is required")
                exam.title = title
            if "topic" in data:
                exam.topic = str(data.get("topic") or "").strip()
            if "instructions" in data:
                exam.instructions = str(data.get("instructions") or "").strip()
            if "duration_minutes" in data:
                exam.duration_minutes = _positive_int(data["duration_minutes"], "duration_minutes", 1440)
            if "class_ids" in data:
                _set_exam_classes(exam, _class_ids_for_teacher(teacher, data["class_ids"]))
            if "status" in data:
                status = str(data["status"])
                if status not in Exam.Status.values:
                    raise ValueError("unsupported status")
                if status == Exam.Status.RUNNING:
                    if not exam.questions.exists():
                        raise ValueError("add at least one question before starting the exam")
                    if not ExamClass.objects.filter(exam=exam).exists():
                        raise ValueError("assign at least one class before starting the exam")
                exam.status = status
            exam.save()
        return JsonResponse({"ok": True, "exam": _serialize_exam(exam, True)})
    except (ValueError, IntegrityError) as exc:
        return _api_error(str(exc), 409 if "locked" in str(exc) else 400)


@_json_errors
@require_http_methods(["GET", "POST"])
def teacher_exam_questions_api(request: HttpRequest, exam_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    exam = get_object_or_404(Exam, id=exam_id, owner=teacher)
    try:
        if request.method == "GET":
            questions = exam.questions.prefetch_related("matching_pairs").order_by("position", "id")
            return JsonResponse({"ok": True, "questions": [_serialize_question_teacher(q) for q in questions]})
        _ensure_exam_editable(exam)
        with transaction.atomic():
            question = _create_question(exam, _json_body(request))
        question = ExamQuestion.objects.prefetch_related("matching_pairs").get(id=question.id)
        return JsonResponse({"ok": True, "question": _serialize_question_teacher(question)}, status=201)
    except (ValueError, IntegrityError) as exc:
        return _api_error(str(exc))


@_json_errors
@require_http_methods(["PATCH", "DELETE"])
def teacher_exam_question_detail_api(request: HttpRequest, question_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    question = get_object_or_404(
        ExamQuestion.objects.select_related("exam").prefetch_related("matching_pairs"),
        id=question_id,
        exam__owner=teacher,
    )
    try:
        _ensure_exam_editable(question.exam)
        if request.method == "DELETE":
            question.delete()
            return JsonResponse({"ok": True})
        with transaction.atomic():
            question = _update_question(question, _json_body(request))
        question = ExamQuestion.objects.prefetch_related("matching_pairs").get(id=question.id)
        return JsonResponse({"ok": True, "question": _serialize_question_teacher(question)})
    except (ValueError, IntegrityError) as exc:
        return _api_error(str(exc), 409 if "locked" in str(exc) else 400)


@_json_errors
@require_POST
def teacher_exam_import_api(request: HttpRequest):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    try:
        data = _json_body(request)
        action = str(data.get("action") or "").strip()
        exam_data = data.get("exam") or {}
        questions = data.get("questions") or []
        if not isinstance(exam_data, dict) or not isinstance(questions, list):
            raise ValueError("exam must be an object and questions must be an array")
        with transaction.atomic():
            if action == "create_exam":
                title = str(exam_data.get("title") or "").strip()
                if not title:
                    raise ValueError("exam.title is required")
                exam = Exam.objects.create(
                    owner=teacher,
                    title=title,
                    topic=str(exam_data.get("topic") or "").strip(),
                    instructions=str(exam_data.get("instructions") or "").strip(),
                    duration_minutes=_positive_int(exam_data.get("duration_minutes", 60), "duration_minutes", 1440),
                )
                _set_exam_classes(exam, _class_ids_for_teacher(teacher, exam_data.get("class_ids", [])))
                for question_data in questions:
                    if not isinstance(question_data, dict):
                        raise ValueError("each question must be an object")
                    _create_question(exam, question_data)
            elif action == "update_exam":
                exam = get_object_or_404(
                    Exam,
                    id=_positive_int(data.get("exam_id"), "exam_id"),
                    owner=teacher,
                )
                _ensure_exam_editable(exam)
                for field in ("topic", "instructions"):
                    if field in exam_data:
                        setattr(exam, field, str(exam_data.get(field) or "").strip())
                if "title" in exam_data:
                    exam.title = str(exam_data.get("title") or "").strip()
                    if not exam.title:
                        raise ValueError("exam.title is required")
                if "duration_minutes" in exam_data:
                    exam.duration_minutes = _positive_int(exam_data["duration_minutes"], "duration_minutes", 1440)
                if "class_ids" in exam_data:
                    _set_exam_classes(exam, _class_ids_for_teacher(teacher, exam_data["class_ids"]))
                exam.save()
                if data.get("replace_questions"):
                    exam.questions.all().delete()
                    for question_data in questions:
                        _create_question(exam, question_data)
                else:
                    for command in questions:
                        if not isinstance(command, dict):
                            raise ValueError("each question command must be an object")
                        command_action = str(command.get("action") or "create")
                        if command_action == "create":
                            _create_question(exam, command)
                        elif command_action in {"update", "delete"}:
                            question = get_object_or_404(
                                ExamQuestion.objects.prefetch_related("matching_pairs"),
                                id=_positive_int(command.get("id"), "question id"),
                                exam=exam,
                            )
                            if command_action == "update":
                                _update_question(question, command)
                            else:
                                question.delete()
                        else:
                            raise ValueError("question action must be create, update, or delete")
            else:
                raise ValueError("action must be create_exam or update_exam")
        exam = Exam.objects.get(id=exam.id)
        return JsonResponse({"ok": True, "exam": _serialize_exam(exam, True)})
    except (ValueError, IntegrityError) as exc:
        return _api_error(str(exc))
    except Exception as exc:
        if exc.__class__.__name__ == "Http404":
            return _api_error("exam or question not found", 404)
        return _api_error("failed to process JSON commands", 500)


@_json_errors
@require_GET
def teacher_exam_attempts_api(request: HttpRequest, exam_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    exam = get_object_or_404(Exam, id=exam_id, owner=teacher)
    attempts = list(
        exam.attempts.select_related("student__class_group")
        .annotate(
            integrity_event_count=Count("integrity_events"),
            last_integrity_at=Max("integrity_events__created_at"),
        )
        .order_by("-started_at")
    )
    for attempt in attempts:
        _expire_if_needed(attempt)
    return JsonResponse({"ok": True, "attempts": [_attempt_summary(a) for a in attempts]})


@_json_errors
@require_GET
def teacher_exam_attempt_detail_api(request: HttpRequest, attempt_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    attempt = get_object_or_404(
        ExamAttempt.objects.select_related("exam", "student__class_group"),
        id=attempt_id,
        exam__owner=teacher,
    )
    answers = {answer.question_id: answer for answer in attempt.answers.all()}
    rows = []
    for question in attempt.exam.questions.prefetch_related("matching_pairs").order_by("position", "id"):
        answer = answers.get(question.id)
        matching_rows = []
        if question.question_type == ExamQuestion.QuestionType.MATCHING:
            presentation = attempt.presentation_json.get(str(question.id), {})
            right_by_key = {
                item["key"]: item["text"] for item in presentation.get("right", [])
            }
            selected = answer.matching_answer if answer else {}
            correct = presentation.get("correct", {})
            matching_rows = [
                {
                    "left_text": item["text"],
                    "selected_right_text": right_by_key.get(selected.get(item["key"]), ""),
                    "is_correct": selected.get(item["key"]) == correct.get(item["key"]),
                }
                for item in presentation.get("left", [])
            ]
        rows.append({
            "question": _serialize_question_teacher(question),
            "answer": _answer_student(answer),
            "answer_id": answer.id if answer else None,
            "awarded_score": float(answer.awarded_score) if answer and answer.awarded_score is not None else None,
            "teacher_feedback": answer.teacher_feedback if answer else "",
            "matching_rows": matching_rows,
        })
    events = [
        _serialize_integrity_event(event)
        for event in attempt.integrity_events.order_by("-created_at")[:100]
    ]
    return JsonResponse({
        "ok": True,
        "attempt": _attempt_summary(attempt),
        "answers": rows,
        "integrity_events": events,
    })


@_json_errors
@require_http_methods(["PATCH"])
def teacher_exam_answer_grade_api(request: HttpRequest, answer_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    answer = get_object_or_404(
        ExamAnswer.objects.select_related("attempt__exam", "question"),
        id=answer_id,
        attempt__exam__owner=teacher,
    )
    try:
        data = _json_body(request)
        awarded = Decimal(str(data.get("awarded_score")))
        if awarded < 0 or awarded > answer.question.max_score:
            raise ValueError("awarded_score is outside the allowed range")
        answer.awarded_score = awarded.quantize(Decimal("0.01"))
        answer.teacher_feedback = str(data.get("teacher_feedback") or "")[:10000]
        answer.save()
        total = answer.attempt.answers.aggregate(total=Sum("awarded_score"))["total"] or Decimal("0")
        answer.attempt.total_score = total
        answer.attempt.save(update_fields=["total_score", "updated_at"])
        return JsonResponse({"ok": True, "total_score": float(total)})
    except (ValueError, InvalidOperation, TypeError) as exc:
        return _api_error(str(exc))


@_json_errors
@require_GET
def student_exams_api(request: HttpRequest):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    exams = (
        Exam.objects.filter(
            Q(status=Exam.Status.RUNNING, allowed_classes=student.class_group)
            | Q(attempts__student=student)
        )
        .distinct()
        .order_by("-created_at")
    )
    attempts = {
        attempt.exam_id: attempt
        for attempt in ExamAttempt.objects.filter(student=student, exam__in=exams)
    }
    rows = []
    for exam in exams:
        attempt = attempts.get(exam.id)
        if attempt:
            _expire_if_needed(attempt)
        rows.append({
            "id": exam.id,
            "title": exam.title,
            "topic": exam.topic,
            "instructions": exam.instructions,
            "duration_minutes": exam.duration_minutes,
            "status": exam.status,
            "question_count": exam.questions.count(),
            "total_points": float(exam.questions.aggregate(total=Sum("max_score"))["total"] or 0),
            "attempt": {
                "id": attempt.id,
                "status": attempt.status,
                "started_at": attempt.started_at.isoformat(),
                "expires_at": attempt.expires_at.isoformat(),
                "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
            } if attempt else None,
        })
    return JsonResponse({"ok": True, "exams": rows})


@_json_errors
@require_POST
def student_exam_start_api(request: HttpRequest, exam_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    try:
        with transaction.atomic():
            exam = get_object_or_404(
                Exam.objects.select_for_update(),
                id=exam_id,
                status=Exam.Status.RUNNING,
                allowed_classes=student.class_group,
            )
            attempt = ExamAttempt.objects.select_for_update().filter(exam=exam, student=student).first()
            if not attempt:
                now = timezone.now()
                attempt = ExamAttempt.objects.create(
                    exam=exam,
                    student=student,
                    started_at=now,
                    expires_at=now + timedelta(minutes=exam.duration_minutes),
                    presentation_json=_presentation_for_exam(exam),
                )
        _expire_if_needed(attempt)
        return JsonResponse({"ok": True, "attempt": _attempt_summary(attempt)})
    except Exception as exc:
        if exc.__class__.__name__ == "Http404":
            return _api_error("exam is not available", 404)
        return _api_error("failed to start exam", 500)


@_json_errors
@require_GET
def student_exam_detail_api(request: HttpRequest, exam_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    attempt = ExamAttempt.objects.select_related("exam", "student__class_group").filter(
        exam_id=exam_id, student=student
    ).first()
    if not attempt:
        return _api_error("start the exam first", 409)
    exam = attempt.exam
    if not ExamClass.objects.filter(exam=exam, class_group=student.class_group).exists():
        return _api_error("exam is unavailable", 403)
    _expire_if_needed(attempt)
    answers = {answer.question_id: answer for answer in attempt.answers.all()}
    questions = [
        _student_question(question, attempt, answers.get(question.id))
        for question in exam.questions.prefetch_related("matching_pairs").order_by("position", "id")
    ]
    return JsonResponse({
        "ok": True,
        "exam": {
            "id": exam.id,
            "title": exam.title,
            "topic": exam.topic,
            "instructions": exam.instructions,
            "duration_minutes": exam.duration_minutes,
            "status": exam.status,
        },
        "attempt": _attempt_summary(attempt),
        "questions": questions,
    })


@_json_errors
@require_POST
def student_exam_answer_api(request: HttpRequest, exam_id: int, question_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)

    data = _json_body(request)
    rate_scope = "exam_diagram_upload" if data.get("upload_to_drive") else "exam_answer_save"
    rate_limit = 30 if data.get("upload_to_drive") else 600
    if request_is_limited(
        rate_scope,
        str(student.id),
        limit=rate_limit,
        window_seconds=3600,
    ):
        response = _api_error("answer save limit exceeded", 429)
        response["Retry-After"] = "3600"
        return response

    try:
        with transaction.atomic():
            attempt = get_object_or_404(
                ExamAttempt.objects.select_for_update().select_related("exam", "student"),
                exam_id=exam_id,
                student=student,
            )
            if attempt.status != ExamAttempt.Status.IN_PROGRESS:
                return _api_error("exam attempt is closed", 409)
            if timezone.now() >= attempt.expires_at:
                attempt.status = ExamAttempt.Status.EXPIRED
                attempt.submitted_at = attempt.expires_at
                attempt.save(update_fields=["status", "submitted_at", "updated_at"])
                return _api_error("exam attempt is closed", 409)

            question = get_object_or_404(
                ExamQuestion,
                id=question_id,
                exam_id=exam_id,
            )
            answer, warning = _save_answer(attempt, question, data)
        return JsonResponse(
            {"ok": True, "answer": _answer_student(answer), "warning": warning}
        )
    except ValueError as exc:
        return _api_error(str(exc))


@_json_errors
@require_POST
def student_exam_integrity_api(request: HttpRequest, exam_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    if request_is_limited(
        "exam_integrity",
        f"{student.id}:{exam_id}",
        limit=180,
        window_seconds=600,
    ):
        response = _api_error("integrity event limit exceeded", 429)
        response["Retry-After"] = "600"
        return response

    data = _json_body(request)
    raw_events = data.get("events")
    if not isinstance(raw_events, list) or not 1 <= len(raw_events) <= 20:
        return _api_error("events must contain between 1 and 20 items")

    try:
        with transaction.atomic():
            attempt = get_object_or_404(
                ExamAttempt.objects.select_for_update(),
                exam_id=exam_id,
                student=student,
            )
            if attempt.status != ExamAttempt.Status.IN_PROGRESS:
                return _api_error("exam attempt is closed", 409)
            if timezone.now() >= attempt.expires_at:
                attempt.status = ExamAttempt.Status.EXPIRED
                attempt.submitted_at = attempt.expires_at
                attempt.save(update_fields=["status", "submitted_at", "updated_at"])
                return _api_error("exam attempt is closed", 409)

            remaining = max(0, 500 - attempt.integrity_events.count())
            clean_events = []
            for raw in raw_events[:remaining]:
                if not isinstance(raw, dict):
                    raise ValueError("every integrity event must be an object")
                event_type = str(raw.get("event_type") or "").strip()
                if event_type not in ExamIntegrityEvent.EventType.values:
                    raise ValueError("unsupported integrity event type")
                client_event_id = str(raw.get("client_event_id") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", client_event_id):
                    raise ValueError("invalid client_event_id")
                detail = re.sub(
                    r"[^A-Za-z0-9+._ -]",
                    "",
                    str(raw.get("detail") or ""),
                )[:120]
                client_at = parse_datetime(str(raw.get("client_at") or ""))
                if client_at and timezone.is_naive(client_at):
                    client_at = timezone.make_aware(client_at)
                clean_events.append(
                    ExamIntegrityEvent(
                        attempt=attempt,
                        event_type=event_type,
                        client_event_id=client_event_id,
                        detail=detail,
                        client_at=client_at,
                    )
                )
            ExamIntegrityEvent.objects.bulk_create(
                clean_events,
                ignore_conflicts=True,
            )
        return JsonResponse({
            "ok": True,
            "accepted": len(clean_events),
            "capped": remaining == 0,
        })
    except ValueError as exc:
        return _api_error(str(exc))

@_json_errors
@require_POST
def student_exam_submit_api(request: HttpRequest, exam_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    try:
        data = _json_body(request)
        with transaction.atomic():
            attempt = get_object_or_404(
                ExamAttempt.objects.select_for_update().select_related("exam", "student"),
                exam_id=exam_id,
                student=student,
            )
            if attempt.status != ExamAttempt.Status.IN_PROGRESS:
                return _api_error("exam attempt is already closed", 409)
            if timezone.now() >= attempt.expires_at:
                attempt.status = ExamAttempt.Status.EXPIRED
                attempt.submitted_at = attempt.expires_at
                attempt.save(update_fields=["status", "submitted_at", "updated_at"])
                return _api_error("exam time has expired; saved drafts were fixed", 409)
            raw_answers = data.get("answers") or []
            if not isinstance(raw_answers, list):
                raise ValueError("answers must be an array")
            questions = {question.id: question for question in attempt.exam.questions.all()}
            for row in raw_answers:
                if not isinstance(row, dict):
                    raise ValueError("each answer must be an object")
                question_id = _positive_int(row.get("question_id"), "question_id")
                question = questions.get(question_id)
                if not question:
                    raise ValueError("answer contains an unknown question")
                final_row = dict(row)
                final_row["upload_to_drive"] = False
                _save_answer(attempt, question, final_row)
            attempt.status = ExamAttempt.Status.SUBMITTED
            attempt.submitted_at = timezone.now()
            attempt.save(update_fields=["status", "submitted_at", "updated_at"])
        return JsonResponse({"ok": True, "attempt": _attempt_summary(attempt)})
    except ValueError as exc:
        return _api_error(str(exc))
    except Exception as exc:
        if exc.__class__.__name__ == "Http404":
            return _api_error("attempt not found", 404)
        return _api_error("failed to submit exam", 500)
