from pathlib import Path

p = Path('core/exam_views.py')
s = p.read_text(encoding='utf-8')
def replace(old, new):
    global s
    assert old in s, old
    s = s.replace(old, new)

replace('        "table_schema": question.table_schema,', '        "table_schema": question.table_schema,\n        "choices": question.choices,')
replace('        "duration_minutes": exam.duration_minutes,', '        "duration_minutes": exam.duration_minutes,\n        "auto_grade": exam.auto_grade,')
replace('        duration_minutes=source.duration_minutes,', '        duration_minutes=source.duration_minutes,\n        auto_grade=source.auto_grade,')
replace('            table_schema=deepcopy(question.table_schema),', '            table_schema=deepcopy(question.table_schema),\n            choices=deepcopy(question.choices),')
replace('def _parse_question_payload(', '''def _parse_choices(raw):
    if not isinstance(raw, list) or not 2 <= len(raw) <= 12:
        raise ValueError("A test needs 2–12 answer options")
    choices = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict) or not isinstance(item.get("is_correct", False), bool):
            raise ValueError("Each option needs text and a boolean is_correct")
        text = str(item.get("text") or "").strip()
        if not text or len(text) > 4000:
            raise ValueError("Option text must contain 1–4000 characters")
        choices.append({"key": str(index), "text": text, "is_correct": item.get("is_correct", False)})
    if sum(c["is_correct"] for c in choices) != 1:
        raise ValueError("Select exactly one correct answer")
    return choices


def _auto_grade_flag(data, default=False):
    value = data.get("auto_grade", default)
    if not isinstance(value, bool):
        raise ValueError("auto_grade must be a boolean")
    return value


def _parse_question_payload(''')
replace('    if question_type == ExamQuestion.QuestionType.TABLE:\n        raw_schema', '''    choices = []
    if question_type == ExamQuestion.QuestionType.SINGLE_CHOICE:
        choices = _parse_choices(data.get("choices", defaults.choices if defaults else []))
        table_schema = {}
        model_answer = next(c["text"] for c in choices if c["is_correct"])
    elif question_type == ExamQuestion.QuestionType.TABLE:
        raw_schema''')
replace('        "pairs": pairs,\n', '        "pairs": pairs,\n        "choices": choices,\n')
start = s.index('def _expire_if_needed(')
end = s.index('\n\ndef _attempt_summary', start)
s = s[:start] + '''def _auto_grade_attempt(attempt):
    if not attempt.exam.auto_grade:
        return
    for question in attempt.exam.questions.filter(question_type__in=[
        ExamQuestion.QuestionType.MATCHING, ExamQuestion.QuestionType.SINGLE_CHOICE,
    ]):
        answer, _ = ExamAnswer.objects.get_or_create(attempt=attempt, question=question)
        if answer.awarded_score is not None:
            continue  # Preserve any explicit teacher grade.
        if question.question_type == ExamQuestion.QuestionType.SINGLE_CHOICE:
            correct = next((c["key"] for c in question.choices if c["is_correct"]), None)
            score = question.max_score if correct and answer.choice_answer == correct else Decimal("0")
        else:
            correct = attempt.presentation_json.get(str(question.id), {}).get("correct", {})
            matched = sum(answer.matching_answer.get(left) == right for left, right in correct.items())
            score = question.max_score * Decimal(matched) / Decimal(len(correct)) if correct else Decimal("0")
        answer.awarded_score = score.quantize(Decimal("0.01"))
        answer.save(update_fields=["awarded_score", "updated_at"])
    attempt.total_score = attempt.answers.aggregate(total=Sum("awarded_score"))["total"] or Decimal("0")
    attempt.save(update_fields=["total_score", "updated_at"])


def _finish_attempt(attempt, status):
    # Serialize closure with autosaves and other requests discovering expiry.
    with transaction.atomic():
        locked = ExamAttempt.objects.select_for_update().select_related("exam").get(id=attempt.id)
        if locked.status == ExamAttempt.Status.IN_PROGRESS:
            locked.status = status
            locked.submitted_at = locked.expires_at if status == ExamAttempt.Status.EXPIRED else timezone.now()
            locked.save(update_fields=["status", "submitted_at", "updated_at"])
            _auto_grade_attempt(locked)
        attempt.status, attempt.submitted_at, attempt.total_score = locked.status, locked.submitted_at, locked.total_score


def _expire_if_needed(attempt: ExamAttempt) -> bool:
    if attempt.status == ExamAttempt.Status.IN_PROGRESS and timezone.now() >= attempt.expires_at:
        _finish_attempt(attempt, ExamAttempt.Status.EXPIRED)
    return attempt.status == ExamAttempt.Status.EXPIRED
''' + s[end:]
replace('            "text_answer": "",', '            "text_answer": "",\n            "choice_answer": "",')
replace('        "text_answer": answer.text_answer,', '        "text_answer": answer.text_answer,\n        "choice_answer": answer.choice_answer,')
replace('    elif question.question_type == ExamQuestion.QuestionType.TABLE:\n        data["table_schema"]', '    elif question.question_type == ExamQuestion.QuestionType.SINGLE_CHOICE:\n        data["choices"] = [{"key": c["key"], "text": c["text"]} for c in question.choices]\n    elif question.question_type == ExamQuestion.QuestionType.TABLE:\n        data["table_schema"]')
replace('    elif question.question_type == ExamQuestion.QuestionType.MATCHING:\n        answer.matching_answer', '''    elif question.question_type == ExamQuestion.QuestionType.SINGLE_CHOICE:
        key = str(data.get("choice_answer") or "")
        choices = {c["key"]: c["text"] for c in question.choices}
        if key and key not in choices:
            raise ValueError("Unknown answer option")
        answer.choice_answer = key
        answer.text_answer = choices.get(key, "")
    elif question.question_type == ExamQuestion.QuestionType.MATCHING:
        answer.matching_answer''')
replace('                duration_minutes=duration,', '                duration_minutes=duration,\n                auto_grade=_auto_grade_flag(data),')
replace('"duration_minutes", "class_ids"}.intersection(data)', '"duration_minutes", "class_ids", "auto_grade"}.intersection(data)')
replace('            if "is_shared_template" in data:', '            if "auto_grade" in data:\n                exam.auto_grade = _auto_grade_flag(data)\n            if "is_shared_template" in data:')
replace('                    duration_minutes=_positive_int(exam_data.get("duration_minutes", 60), "duration_minutes", 1440),', '                    duration_minutes=_positive_int(exam_data.get("duration_minutes", 60), "duration_minutes", 1440),\n                    auto_grade=_auto_grade_flag(exam_data),')
replace('                if "class_ids" in exam_data:', '                if "auto_grade" in exam_data:\n                    exam.auto_grade = _auto_grade_flag(exam_data)\n                if "class_ids" in exam_data:')
replace('                attempt.status = ExamAttempt.Status.EXPIRED\n                attempt.submitted_at = attempt.expires_at\n                attempt.save(update_fields=["status", "submitted_at", "updated_at"])', '                _finish_attempt(attempt, ExamAttempt.Status.EXPIRED)')
replace('            attempt.status = ExamAttempt.Status.SUBMITTED\n            attempt.submitted_at = timezone.now()\n            attempt.save(update_fields=["status", "submitted_at", "updated_at"])', '            _finish_attempt(attempt, ExamAttempt.Status.SUBMITTED)')
# Opening a teacher review must fix expired drafts before reading scores.
needle = '    answers = {answer.question_id: answer for answer in attempt.answers.all()}\n    rows = []'
replace(needle, '    _expire_if_needed(attempt)\n' + needle)
p.write_text(s, encoding='utf-8')

p = Path('core/exam_reports.py')
s = p.read_text(encoding='utf-8').replace('matching_pairs,image_url"','matching_pairs,image_url,choices"').replace('q.image_url] for q in data["questions"]]', 'q.image_url, q.choices] for q in data["questions"]]')
p.write_text(s, encoding='utf-8')

p = Path('core/templates/core/student_exam_result.html')
s = p.read_text(encoding='utf-8').replace("row.question.question_type == 'open_text'", "row.question.question_type == 'open_text' or row.question.question_type == 'single_choice'")
p.write_text(s, encoding='utf-8')
