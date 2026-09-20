import random
from collections import Counter
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import urlparse

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .exam_views import (
    _api_error,
    _json_body,
    _student,
    _student_image_url,
    _student_required,
    _teacher,
    _teacher_required,
)
from .models import (
    ClassGroup,
    Exam,
    ExamAttempt,
    ExamQuestion,
    PeerAssessmentAssignment,
    PeerAssessmentReview,
    PeerAssessmentSession,
    Student,
    StudentClassMembership,
)


def _json_errors(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except Http404:
            return _api_error("resource not found", 404)
        except ValueError as exc:
            return _api_error(str(exc), 400)
        except IntegrityError:
            return _api_error("database conflict", 409)
        except Exception:
            return _api_error("internal server error", 500)

    return wrapped


def _owned_class(teacher, class_id):
    return get_object_or_404(ClassGroup, id=class_id, owner=teacher)


def _student_class_ids(student):
    class_ids = set(
        StudentClassMembership.objects.filter(student=student).values_list(
            "class_group_id", flat=True
        )
    )
    if student.class_group_id:
        class_ids.add(student.class_group_id)
    return sorted(class_ids)


def _owned_session(teacher, session_id):
    return get_object_or_404(
        PeerAssessmentSession.objects.select_related("reviewer_class").prefetch_related("allowed_exams"),
        id=session_id,
        owner=teacher,
    )


def _positive_int(value, field_name, maximum=100):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")
    return parsed


def _attempt_queryset(teacher):
    return ExamAttempt.objects.select_related("exam", "student__class_group").filter(
        exam__owner=teacher,
        status__in=[ExamAttempt.Status.SUBMITTED, ExamAttempt.Status.EXPIRED],
    )


def _owned_exams(teacher, raw_ids):
    if raw_ids is None:
        return []
    if not isinstance(raw_ids, list):
        raise ValueError("exam_ids must be a list")
    exam_ids = {
        _positive_int(value, "exam_id", 2_000_000_000)
        for value in raw_ids
    }
    exams = list(Exam.objects.filter(owner=teacher, id__in=exam_ids).order_by("title", "id"))
    if len(exams) != len(exam_ids):
        raise ValueError("one or more exams are unavailable")
    return exams


def _exam_filter_rows(teacher):
    exams = (
        Exam.objects.filter(owner=teacher)
        .annotate(
            completed_attempt_count=Count(
                "attempts",
                filter=Q(
                    attempts__status__in=[
                        ExamAttempt.Status.SUBMITTED,
                        ExamAttempt.Status.EXPIRED,
                    ]
                ),
            )
        )
        .order_by("title", "id")
    )
    return [
        {
            "id": exam.id,
            "title": exam.title,
            "topic": exam.topic,
            "completed_attempt_count": exam.completed_attempt_count,
        }
        for exam in exams
    ]


def _public_https_url(value):
    value = str(value or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return value


def _attempt_row(attempt):
    return {
        "id": attempt.id,
        "exam_id": attempt.exam_id,
        "exam_title": attempt.exam.title,
        "student_id": attempt.student_id,
        "student_name": attempt.student.full_name,
        "class_id": attempt.student.class_group_id,
        "class_name": attempt.student.class_group.name,
        "status": attempt.status,
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
    }


def _assignment_row(assignment, include_identity=True):
    data = {
        "id": assignment.id,
        "attempt_id": assignment.exam_attempt_id,
        "exam_id": assignment.exam_attempt.exam_id,
        "exam_title": assignment.exam_attempt.exam.title,
        "question_count": assignment.exam_attempt.exam.questions.count(),
        "reviewed_count": assignment.reviews.count(),
    }
    if include_identity:
        data.update(
            {
                "author_name": assignment.exam_attempt.student.full_name,
                "author_class": assignment.exam_attempt.student.class_group.name,
            }
        )
    return data


def _session_row(session, include_students=False):
    data = {
        "id": session.id,
        "title": session.title,
        "status": session.status,
        "reviewer_class": {
            "id": session.reviewer_class_id,
            "name": session.reviewer_class.name,
        },
        "assignment_count": session.assignments.count(),
        "allowed_exams": [
            {"id": exam.id, "title": exam.title}
            for exam in session.allowed_exams.all()
        ],
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }
    if not include_students:
        return data

    assignments = list(
        session.assignments.select_related(
            "reviewer",
            "exam_attempt__exam",
            "exam_attempt__student__class_group",
        ).prefetch_related("reviews", "exam_attempt__exam__questions")
    )
    by_reviewer = {}
    for assignment in assignments:
        by_reviewer.setdefault(assignment.reviewer_id, []).append(_assignment_row(assignment))

    data["students"] = [
        {
            "id": student.id,
            "full_name": student.full_name,
            "assignments": by_reviewer.get(student.id, []),
        }
        for student in Student.objects.filter(
            class_group=session.reviewer_class,
            is_active=True,
        ).order_by("full_name", "id")
    ]

    reviews = PeerAssessmentReview.objects.filter(
        assignment__session=session,
    ).select_related(
        "assignment__reviewer",
        "assignment__exam_attempt__student__class_group",
        "assignment__exam_attempt__exam",
        "question",
    ).order_by("assignment__reviewer__full_name", "assignment_id", "question__position")
    data["reviews"] = [
        {
            "id": review.id,
            "assignment_id": review.assignment_id,
            "attempt_id": review.assignment.exam_attempt_id,
            "reviewer_id": review.assignment.reviewer_id,
            "reviewer_name": review.assignment.reviewer.full_name,
            "author_name": review.assignment.exam_attempt.student.full_name,
            "author_class": review.assignment.exam_attempt.student.class_group.name,
            "exam_title": review.assignment.exam_attempt.exam.title,
            "question_id": review.question_id,
            "question_position": review.question.position,
            "question_prompt": review.question.prompt,
            "score": float(review.score),
            "max_score": float(review.question.max_score),
            "comment": review.comment,
            "moderation_status": review.moderation_status,
            "teacher_comment": review.teacher_comment,
            "updated_at": review.updated_at.isoformat(),
        }
        for review in reviews
    ]

    works_by_attempt = {}
    for assignment in assignments:
        work = works_by_attempt.setdefault(
            assignment.exam_attempt_id,
            {
                "attempt_id": assignment.exam_attempt_id,
                "exam_id": assignment.exam_attempt.exam_id,
                "exam_title": assignment.exam_attempt.exam.title,
                "author_name": assignment.exam_attempt.student.full_name,
                "author_class": assignment.exam_attempt.student.class_group.name,
                "question_count": assignment.exam_attempt.exam.questions.count(),
                "reviewer_count": 0,
                "completed_reviewer_count": 0,
                "review_count": 0,
            },
        )
        review_count = assignment.reviews.count()
        work["reviewer_count"] += 1
        work["review_count"] += review_count
        if work["question_count"] and review_count >= work["question_count"]:
            work["completed_reviewer_count"] += 1
    data["result_works"] = sorted(
        works_by_attempt.values(),
        key=lambda row: (row["exam_title"].casefold(), row["author_name"].casefold(), row["attempt_id"]),
    )
    return data


def _matching_answer_rows(attempt, question, answer):
    presentation = attempt.presentation_json.get(str(question.id), {})
    right_by_key = {
        str(item.get("key")): str(item.get("text") or "")
        for item in presentation.get("right", [])
    }
    selected = answer.matching_answer if answer else {}
    return [
        {
            "left_text": str(item.get("text") or ""),
            "right_text": right_by_key.get(str(selected.get(str(item.get("key")))), ""),
        }
        for item in presentation.get("left", [])
    ]


def _question_row(assignment, question, answer, review):
    mark_scheme = {
        "model_answer": question.model_answer,
        "matching_pairs": [
            {"left_text": pair.left_text, "right_text": pair.right_text}
            for pair in question.matching_pairs.all()
        ],
        "table_schema": question.table_schema,
    }
    source_answer = {
        "text_answer": answer.text_answer if answer else "",
        "matching_rows": _matching_answer_rows(assignment.exam_attempt, question, answer),
        "diagram_file_url": _public_https_url(answer.diagram_file_url) if answer else "",
        "table_answer": answer.table_answer if answer else {},
    }
    return {
        "id": question.id,
        "position": question.position,
        "question_type": question.question_type,
        "prompt": question.prompt,
        "image_url": _student_image_url(question.image_url),
        "max_score": float(question.max_score),
        "table_schema": question.table_schema,
        "answer": source_answer,
        "mark_scheme": mark_scheme,
        "review": {
            "id": review.id,
            "score": float(review.score),
            "comment": review.comment,
            "moderation_status": review.moderation_status,
            "teacher_comment": review.teacher_comment,
        } if review else None,
    }


@_teacher_required
@ensure_csrf_cookie
def teacher_peer_assessment_page(request: HttpRequest):
    return render(request, "core/teacher/peer_assessment.html", {"active": "assessment"})


@_student_required
@ensure_csrf_cookie
def student_peer_assessment_page(request: HttpRequest):
    return render(request, "core/student_peer_assessment.html")


@_teacher_required
@ensure_csrf_cookie
def teacher_peer_result_page(request: HttpRequest, session_id: int, attempt_id: int):
    teacher = _teacher(request)
    session = _owned_session(teacher, session_id)
    assignments = list(
        PeerAssessmentAssignment.objects.filter(
            session=session,
            exam_attempt_id=attempt_id,
        )
        .select_related(
            "reviewer",
            "exam_attempt__exam",
            "exam_attempt__student__class_group",
        )
        .prefetch_related("reviews")
    )
    if not assignments:
        raise Http404

    attempt = assignments[0].exam_attempt
    answers = {answer.question_id: answer for answer in attempt.answers.all()}
    reviews_by_question = {}
    reviewer_totals = []
    for assignment in assignments:
        assignment_reviews = list(assignment.reviews.all())
        reviewer_totals.append({
            "reviewer_name": assignment.reviewer.full_name,
            "score": float(sum((review.score for review in assignment_reviews), Decimal("0"))),
            "reviewed_count": len(assignment_reviews),
        })
        for review in assignment_reviews:
            reviews_by_question.setdefault(review.question_id, []).append({
                "id": review.id,
                "reviewer_name": assignment.reviewer.full_name,
                "score": float(review.score),
                "comment": review.comment,
                "moderation_status": review.moderation_status,
                "teacher_comment": review.teacher_comment,
            })

    questions = list(
        attempt.exam.questions.prefetch_related("matching_pairs").order_by("position", "id")
    )
    question_rows = []
    maximum_score = sum((question.max_score for question in questions), Decimal("0"))
    teacher_scores = []
    for question in questions:
        answer = answers.get(question.id)
        if answer and answer.awarded_score is not None:
            teacher_scores.append(answer.awarded_score)
        row = _question_row(assignments[0], question, answer, None)
        row["teacher_assessment"] = {
            "score": float(answer.awarded_score) if answer and answer.awarded_score is not None else None,
            "feedback": answer.teacher_feedback if answer else "",
        }
        row["peer_reviews"] = reviews_by_question.get(question.id, [])
        question_rows.append(row)

    result = {
        "session_id": session.id,
        "session_title": session.title,
        "attempt_id": attempt.id,
        "exam_title": attempt.exam.title,
        "author_name": attempt.student.full_name,
        "author_class": attempt.student.class_group.name,
        "maximum_score": float(maximum_score),
        "teacher_score": float(sum(teacher_scores, Decimal("0"))) if teacher_scores else None,
        "question_count": len(questions),
        "reviewer_totals": reviewer_totals,
        "questions": question_rows,
    }
    return render(
        request,
        "core/teacher/peer_result_detail.html",
        {"active": "assessment", "result": result},
    )


@_json_errors
@require_http_methods(["GET", "POST"])
def teacher_peer_sessions_api(request: HttpRequest):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    if request.method == "GET":
        sessions = (
            PeerAssessmentSession.objects.filter(owner=teacher)
            .select_related("reviewer_class")
            .prefetch_related("allowed_exams")
        )
        return JsonResponse({
            "ok": True,
            "sessions": [_session_row(row) for row in sessions],
            "exams": _exam_filter_rows(teacher),
        })

    data = _json_body(request)
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    class_id = data.get("class_id")
    if class_id:
        reviewer_class = _owned_class(teacher, class_id)
    else:
        reviewer_class = ClassGroup.objects.filter(owner=teacher).order_by("name", "id").last()
        if not reviewer_class:
            raise ValueError("create a class first")
    allowed_exams = _owned_exams(teacher, data.get("exam_ids"))
    with transaction.atomic():
        session = PeerAssessmentSession.objects.create(
            owner=teacher,
            title=title[:200],
            reviewer_class=reviewer_class,
        )
        session.allowed_exams.set(allowed_exams)
    return JsonResponse({"ok": True, "session": _session_row(session, True)}, status=201)


@_json_errors
@require_http_methods(["GET", "PATCH", "DELETE"])
def teacher_peer_session_detail_api(request: HttpRequest, session_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    session = _owned_session(teacher, session_id)
    if request.method == "GET":
        return JsonResponse({"ok": True, "session": _session_row(session, True)})
    if request.method == "DELETE":
        if session.status == PeerAssessmentSession.Status.RUNNING:
            raise ValueError("stop the session before deleting it")
        session.delete()
        return JsonResponse({"ok": True})

    data = _json_body(request)
    if "title" in data:
        title = str(data.get("title") or "").strip()
        if not title:
            raise ValueError("title is required")
        session.title = title[:200]
    if "class_id" in data:
        reviewer_class = _owned_class(teacher, data["class_id"])
        if reviewer_class.id != session.reviewer_class_id and session.assignments.exists():
            raise ValueError("remove assignments before changing the reviewer class")
        session.reviewer_class = reviewer_class
    if "status" in data:
        status = str(data.get("status") or "")
        if status not in PeerAssessmentSession.Status.values:
            raise ValueError("unsupported status")
        if status == PeerAssessmentSession.Status.RUNNING and not session.assignments.exists():
            raise ValueError("assign at least one work before starting")
        session.status = status
    exams = None
    if "exam_ids" in data:
        exams = _owned_exams(teacher, data.get("exam_ids"))
        exam_ids = {exam.id for exam in exams}
        if exam_ids and session.assignments.exclude(exam_attempt__exam_id__in=exam_ids).exists():
            raise ValueError("remove assignments outside the selected exams before changing the filter")
    with transaction.atomic():
        session.save()
        if exams is not None:
            session.allowed_exams.set(exams)
    return JsonResponse({"ok": True, "session": _session_row(session, True)})


@_json_errors
@require_GET
def teacher_peer_attempt_search_api(request: HttpRequest):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    queryset = _attempt_queryset(teacher)
    session_id = request.GET.get("session_id")
    if session_id:
        session = _owned_session(
            teacher,
            _positive_int(session_id, "session_id", 2_000_000_000),
        )
        allowed_exam_ids = list(session.allowed_exams.values_list("id", flat=True))
        if allowed_exam_ids:
            queryset = queryset.filter(exam_id__in=allowed_exam_ids)
    reviewer_id = request.GET.get("reviewer_id")
    if reviewer_id:
        reviewer = get_object_or_404(Student, id=reviewer_id, class_group__owner=teacher)
        queryset = queryset.exclude(student=reviewer)
    source_class_id = request.GET.get("class_id")
    if source_class_id:
        source_class = _owned_class(teacher, source_class_id)
        queryset = queryset.filter(student__class_group=source_class)
    query = str(request.GET.get("q") or "").strip()
    if query:
        queryset = queryset.filter(
            Q(student__full_name__icontains=query)
            | Q(exam__title__icontains=query)
            | Q(student__class_group__name__icontains=query)
        )
    attempts = queryset.order_by("-submitted_at", "-id")[:50]
    return JsonResponse({"ok": True, "attempts": [_attempt_row(row) for row in attempts]})


@_json_errors
@require_POST
def teacher_peer_assignments_api(request: HttpRequest, session_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    session = _owned_session(teacher, session_id)
    data = _json_body(request)
    reviewer = get_object_or_404(
        Student,
        id=_positive_int(data.get("reviewer_id"), "reviewer_id", 2_000_000_000),
        class_group=session.reviewer_class,
        is_active=True,
    )
    attempt = get_object_or_404(
        _attempt_queryset(teacher),
        id=_positive_int(data.get("attempt_id"), "attempt_id", 2_000_000_000),
    )
    allowed_exam_ids = set(session.allowed_exams.values_list("id", flat=True))
    if allowed_exam_ids and attempt.exam_id not in allowed_exam_ids:
        raise ValueError("the exam attempt is outside this assessment session filter")
    if attempt.student_id == reviewer.id:
        raise ValueError("students cannot review their own work")
    assignment, created = PeerAssessmentAssignment.objects.get_or_create(
        session=session,
        reviewer=reviewer,
        exam_attempt=attempt,
    )
    return JsonResponse(
        {"ok": True, "created": created, "assignment": _assignment_row(assignment)},
        status=201 if created else 200,
    )


@_json_errors
@require_http_methods(["DELETE"])
def teacher_peer_assignment_detail_api(request: HttpRequest, assignment_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    assignment = get_object_or_404(
        PeerAssessmentAssignment,
        id=assignment_id,
        session__owner=teacher,
    )
    if assignment.reviews.exists():
        raise ValueError("an assignment with saved reviews cannot be removed")
    assignment.delete()
    return JsonResponse({"ok": True})


@_json_errors
@require_POST
def teacher_peer_autofill_api(request: HttpRequest, session_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    session = _owned_session(teacher, session_id)
    data = _json_body(request)
    count = _positive_int(data.get("count", 1), "count", 20)
    source_class = None
    if data.get("source_class_id"):
        source_class = _owned_class(teacher, data["source_class_id"])

    candidate_queryset = _attempt_queryset(teacher).filter(
        **({"student__class_group": source_class} if source_class else {})
    )
    allowed_exam_ids = list(session.allowed_exams.values_list("id", flat=True))
    if allowed_exam_ids:
        candidate_queryset = candidate_queryset.filter(exam_id__in=allowed_exam_ids)
    candidates = list(candidate_queryset)
    reviewers = list(Student.objects.filter(
        class_group=session.reviewer_class,
        is_active=True,
    ).order_by("id"))
    if not reviewers:
        raise ValueError("the reviewer class has no active students")
    if not candidates:
        raise ValueError("no completed exam attempts are available")

    created_count = 0
    with transaction.atomic():
        session = PeerAssessmentSession.objects.select_for_update().get(
            id=session.id,
            owner=teacher,
        )
        removable = session.assignments.annotate(review_count=Count("reviews")).filter(review_count=0)
        removable.delete()
        remaining_assignments = list(
            session.assignments.only(
                "reviewer_id",
                "exam_attempt_id",
            )
        )
        existing = {reviewer.id: set() for reviewer in reviewers}
        reviewer_loads = Counter()
        incoming_loads = Counter()
        for assignment in remaining_assignments:
            incoming_loads[assignment.exam_attempt_id] += 1
            if assignment.reviewer_id in existing:
                existing[assignment.reviewer_id].add(assignment.exam_attempt_id)
                reviewer_loads[assignment.reviewer_id] += 1

        randomizer = random.SystemRandom()
        rows = []
        while True:
            underloaded = [
                reviewer
                for reviewer in reviewers
                if reviewer_loads[reviewer.id] < count
            ]
            if not underloaded:
                break
            randomizer.shuffle(underloaded)
            created_in_pass = False
            for reviewer in underloaded:
                available = [
                    attempt
                    for attempt in candidates
                    if attempt.student_id != reviewer.id
                    and attempt.id not in existing[reviewer.id]
                ]
                if not available:
                    continue
                minimum_load = min(incoming_loads[attempt.id] for attempt in available)
                least_assigned = [
                    attempt
                    for attempt in available
                    if incoming_loads[attempt.id] == minimum_load
                ]
                attempt = randomizer.choice(least_assigned)
                rows.append(PeerAssessmentAssignment(
                    session=session,
                    reviewer=reviewer,
                    exam_attempt=attempt,
                ))
                existing[reviewer.id].add(attempt.id)
                reviewer_loads[reviewer.id] += 1
                incoming_loads[attempt.id] += 1
                created_in_pass = True
            if not created_in_pass:
                break
        PeerAssessmentAssignment.objects.bulk_create(rows)
        created_count = len(rows)

    return JsonResponse({
        "ok": True,
        "created_count": created_count,
        "session": _session_row(session, True),
    })


@_json_errors
@require_http_methods(["PATCH"])
def teacher_peer_review_moderate_api(request: HttpRequest, review_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    review = get_object_or_404(
        PeerAssessmentReview,
        id=review_id,
        assignment__session__owner=teacher,
    )
    data = _json_body(request)
    status = str(data.get("moderation_status") or "")
    if status not in PeerAssessmentReview.ModerationStatus.values:
        raise ValueError("unsupported moderation status")
    review.moderation_status = status
    review.teacher_comment = str(data.get("teacher_comment") or "")[:10000]
    review.save(update_fields=["moderation_status", "teacher_comment", "updated_at"])
    return JsonResponse({"ok": True})


@_json_errors
@require_GET
def student_peer_sessions_api(request: HttpRequest):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    sessions = PeerAssessmentSession.objects.filter(
        reviewer_class_id__in=_student_class_ids(student),
        assignments__reviewer=student,
    ).select_related("reviewer_class").distinct()
    rows = []
    for session in sessions:
        assignments = session.assignments.filter(reviewer=student).select_related("exam_attempt__exam")
        rows.append({
            "id": session.id,
            "title": session.title,
            "status": session.status,
            "assignments": [_assignment_row(row, include_identity=False) for row in assignments],
        })
    return JsonResponse({"ok": True, "sessions": rows})


@_json_errors
@require_GET
def student_peer_assignment_detail_api(request: HttpRequest, assignment_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    assignment = get_object_or_404(
        PeerAssessmentAssignment.objects.select_related("session", "exam_attempt__exam"),
        id=assignment_id,
        reviewer=student,
        session__reviewer_class_id__in=_student_class_ids(student),
    )
    answers = {
        answer.question_id: answer
        for answer in assignment.exam_attempt.answers.all()
    }
    reviews = {
        review.question_id: review
        for review in assignment.reviews.all()
    }
    questions = [
        _question_row(assignment, question, answers.get(question.id), reviews.get(question.id))
        for question in assignment.exam_attempt.exam.questions.prefetch_related("matching_pairs").order_by("position", "id")
    ]
    return JsonResponse({
        "ok": True,
        "assignment": {
            "id": assignment.id,
            "attempt_id": assignment.exam_attempt_id,
            "exam_title": assignment.exam_attempt.exam.title,
            "session_id": assignment.session_id,
            "session_title": assignment.session.title,
            "session_status": assignment.session.status,
            "questions": questions,
        },
    })


@_json_errors
@require_POST
def student_peer_review_api(request: HttpRequest, assignment_id: int, question_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    assignment = get_object_or_404(
        PeerAssessmentAssignment.objects.select_related("session", "exam_attempt__exam"),
        id=assignment_id,
        reviewer=student,
        session__reviewer_class_id__in=_student_class_ids(student),
    )
    if assignment.session.status != PeerAssessmentSession.Status.RUNNING:
        raise ValueError("the assessment session is not running")
    question = get_object_or_404(
        ExamQuestion,
        id=question_id,
        exam=assignment.exam_attempt.exam,
    )
    data = _json_body(request)
    try:
        score = Decimal(str(data.get("score"))).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("score must be a number") from exc
    if score < 0 or score > question.max_score:
        raise ValueError("score is outside the allowed range")
    comment = str(data.get("comment") or "")[:10000]
    review, created = PeerAssessmentReview.objects.get_or_create(
        assignment=assignment,
        question=question,
        defaults={"score": score, "comment": comment},
    )
    if not created and (review.score != score or review.comment != comment):
        review.score = score
        review.comment = comment
        review.moderation_status = PeerAssessmentReview.ModerationStatus.PENDING
        review.teacher_comment = ""
        review.save()
    return JsonResponse({
        "ok": True,
        "review": {
            "id": review.id,
            "score": float(review.score),
            "comment": review.comment,
            "moderation_status": review.moderation_status,
        },
    })
