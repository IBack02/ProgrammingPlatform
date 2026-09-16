import logging
import re
import unicodedata
from urllib.parse import urlparse

from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .exam_views import _json_body, _teacher
from .models import (
    GameModule,
    MindRacePrompt,
    Session,
    SessionTask,
    TaskCodeFragment,
    TaskTestCase,
    TheoryMaterialBlock,
    TheoryMaterialModule,
    TheoryQuizChoice,
    TheoryQuizMatchPair,
    TheoryQuizModule,
    TheoryQuizQuestion,
    TournamentQuestion,
    TournamentStage,
    WonderFieldQuestion,
)


logger = logging.getLogger(__name__)

MAX_MODULES = 100
MAX_CHILDREN = 500
MAX_LONG_TEXT = 100_000
DRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtube-nocookie.com",
}


def _api_error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


def _reject_unknown(data: dict, allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{context} contains unknown fields: {', '.join(unknown)}")


def _object(value, field_name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _array(value, field_name: str, *, maximum: int = MAX_CHILDREN) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    if len(value) > maximum:
        raise ValueError(f"{field_name} cannot contain more than {maximum} items")
    return value


def _text(
    value,
    field_name: str,
    *,
    maximum: int = MAX_LONG_TEXT,
    required: bool = False,
    strip: bool = True,
) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    parsed = value.strip() if strip else value
    if required and not parsed.strip():
        raise ValueError(f"{field_name} is required")
    if len(parsed) > maximum:
        raise ValueError(f"{field_name} is too long")
    return parsed


def _boolean(value, field_name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be true or false")
    return value


def _positive_int(value, field_name: str, *, maximum: int = 10_000) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")
    return parsed


def _ordinal_rows(value, field_name: str) -> list[tuple[int, dict]]:
    rows = _array(value, field_name)
    parsed_rows = []
    used = set()
    for index, row in enumerate(rows, start=1):
        row = _object(row, f"{field_name}[{index - 1}]")
        ordinal = _positive_int(row.get("ordinal", index), f"{field_name}[{index - 1}].ordinal")
        if ordinal in used:
            raise ValueError(f"{field_name} contains duplicate ordinal {ordinal}")
        used.add(ordinal)
        parsed_rows.append((ordinal, row))
    return parsed_rows


def _https_url(value, field_name: str, *, allowed_hosts: set[str] | None = None) -> str:
    url = _text(value, field_name, maximum=2_000, required=True)
    parsed = urlparse(url)
    has_unsafe_chars = any(ord(char) < 32 or char in '"<>\\' for char in url)
    hostname = (parsed.hostname or "").lower()
    if (
        has_unsafe_chars
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or (allowed_hosts is not None and hostname not in allowed_hosts)
    ):
        raise ValueError(f"{field_name} must be a permitted HTTPS URL")
    return url


def _existing_positions(session: Session) -> set[int]:
    positions = set(SessionTask.objects.filter(session=session).values_list("position", flat=True))
    positions.update(
        TheoryMaterialModule.objects.filter(session=session).values_list("position", flat=True)
    )
    positions.update(
        TheoryQuizModule.objects.filter(session=session).values_list("position", flat=True)
    )
    positions.update(GameModule.objects.filter(session=session).values_list("position", flat=True))
    return positions


def _module_position(raw_value, used_positions: set[int], field_name: str) -> int:
    if raw_value in (None, "", "auto"):
        position = 1
        while position in used_positions:
            position += 1
    else:
        position = _positive_int(raw_value, field_name)
    if position in used_positions:
        raise ValueError(f"module position {position} is already used in this session")
    used_positions.add(position)
    return position


def _prepare_hints(value) -> dict:
    hints = _object(value, "hints")
    _reject_unknown(hints, {"enabled", "level_1", "level_2", "level_3"}, "hints")
    result = {"hints_enabled": _boolean(hints.get("enabled"), "hints.enabled", False)}
    defaults = {1: 2, 2: 3, 3: 3}
    for level in (1, 2, 3):
        key = f"level_{level}"
        row = _object(hints.get(key), f"hints.{key}")
        _reject_unknown(row, {"enabled", "unlock_attempts"}, f"hints.{key}")
        result[f"hint{level}_enabled"] = _boolean(
            row.get("enabled"), f"hints.{key}.enabled", True
        )
        result[f"hint{level}_unlock_attempts"] = _positive_int(
            row.get("unlock_attempts", defaults[level]),
            f"hints.{key}.unlock_attempts",
            maximum=100,
        )
    return result


def _prepare_testcases(value) -> list[dict]:
    result = []
    for ordinal, row in _ordinal_rows(value, "testcases"):
        _reject_unknown(
            row,
            {"ordinal", "stdin", "expected_stdout", "is_visible"},
            f"testcases[{ordinal}]",
        )
        result.append(
            {
                "ordinal": ordinal,
                "stdin": _text(row.get("stdin"), f"testcases[{ordinal}].stdin", strip=False),
                "expected_stdout": _text(
                    row.get("expected_stdout"),
                    f"testcases[{ordinal}].expected_stdout",
                    strip=False,
                ),
                "is_visible": _boolean(
                    row.get("is_visible"), f"testcases[{ordinal}].is_visible", False
                ),
            }
        )
    return result


def _prepare_fragments(value) -> list[dict]:
    rows = _array(value, "fragments")
    result = []
    for index, raw in enumerate(rows):
        row = _object(raw, f"fragments[{index}]")
        _reject_unknown(row, {"position", "title", "code", "is_active"}, f"fragments[{index}]")
        position = _text(row.get("position"), f"fragments[{index}].position", required=True)
        if position not in TaskCodeFragment.Position.values:
            raise ValueError(f"fragments[{index}].position must be top or bottom")
        result.append(
            {
                "position": position,
                "title": _text(row.get("title"), f"fragments[{index}].title", maximum=120),
                "code": _text(
                    row.get("code"), f"fragments[{index}].code", required=True, strip=False
                ),
                "is_active": _boolean(
                    row.get("is_active"), f"fragments[{index}].is_active", True
                ),
            }
        )
    return result


def _prepare_theory_blocks(value) -> list[dict]:
    allowed_types = set(TheoryMaterialBlock.BlockType.values)
    result = []
    for ordinal, row in _ordinal_rows(value, "blocks"):
        _reject_unknown(row, {"ordinal", "block_type", "heading_level", "content"}, f"blocks[{ordinal}]")
        block_type = _text(row.get("block_type"), f"blocks[{ordinal}].block_type", required=True)
        if block_type not in allowed_types:
            raise ValueError(f"blocks[{ordinal}].block_type is invalid")
        content = _text(
            row.get("content"), f"blocks[{ordinal}].content", required=True, strip=False
        ).rstrip()
        heading_level = _text(row.get("heading_level"), f"blocks[{ordinal}].heading_level", maximum=8)
        if block_type == TheoryMaterialBlock.BlockType.HEADING:
            if heading_level not in TheoryMaterialBlock.HeadingLevel.values:
                raise ValueError(f"blocks[{ordinal}].heading_level must be h1 or h2")
        else:
            heading_level = ""
        if block_type == TheoryMaterialBlock.BlockType.IMAGE:
            content = _https_url(content, f"blocks[{ordinal}].content")
        elif block_type == TheoryMaterialBlock.BlockType.VIDEO:
            content = _https_url(
                content, f"blocks[{ordinal}].content", allowed_hosts=YOUTUBE_HOSTS
            )
        elif block_type == TheoryMaterialBlock.BlockType.ATTACHMENT:
            content = _https_url(
                content, f"blocks[{ordinal}].content", allowed_hosts=DRIVE_HOSTS
            )
        result.append(
            {
                "ordinal": ordinal,
                "block_type": block_type,
                "heading_level": heading_level,
                "content": content,
            }
        )
    return result


def _prepare_choices(value, question_ordinal: int) -> list[dict]:
    rows = _ordinal_rows(value, f"questions[{question_ordinal}].choices")
    if len(rows) not in {3, 4}:
        raise ValueError(f"questions[{question_ordinal}].choices must contain 3 or 4 items")
    result = []
    correct_count = 0
    for ordinal, row in rows:
        _reject_unknown(
            row,
            {"ordinal", "text", "is_correct"},
            f"questions[{question_ordinal}].choices[{ordinal}]",
        )
        is_correct = _boolean(
            row.get("is_correct"),
            f"questions[{question_ordinal}].choices[{ordinal}].is_correct",
            False,
        )
        correct_count += int(is_correct)
        result.append(
            {
                "ordinal": ordinal,
                "text": _text(
                    row.get("text"),
                    f"questions[{question_ordinal}].choices[{ordinal}].text",
                    required=True,
                ),
                "is_correct": is_correct,
            }
        )
    if correct_count != 1:
        raise ValueError(f"questions[{question_ordinal}] must have exactly one correct choice")
    return result


def _prepare_pairs(value, question_ordinal: int) -> list[dict]:
    rows = _ordinal_rows(value, f"questions[{question_ordinal}].pairs")
    if len(rows) < 2:
        raise ValueError(f"questions[{question_ordinal}].pairs must contain at least 2 items")
    result = []
    for ordinal, row in rows:
        _reject_unknown(
            row,
            {"ordinal", "left_text", "right_text"},
            f"questions[{question_ordinal}].pairs[{ordinal}]",
        )
        result.append(
            {
                "ordinal": ordinal,
                "left_text": _text(
                    row.get("left_text"),
                    f"questions[{question_ordinal}].pairs[{ordinal}].left_text",
                    required=True,
                ),
                "right_text": _text(
                    row.get("right_text"),
                    f"questions[{question_ordinal}].pairs[{ordinal}].right_text",
                    required=True,
                ),
            }
        )
    return result


def _prepare_quiz_questions(value) -> list[dict]:
    result = []
    for ordinal, row in _ordinal_rows(value, "questions"):
        _reject_unknown(
            row,
            {
                "ordinal",
                "question_type",
                "prompt",
                "model_answer",
                "accept_suitable_answer",
                "choices",
                "pairs",
            },
            f"questions[{ordinal}]",
        )
        question_type = _text(
            row.get("question_type"), f"questions[{ordinal}].question_type", required=True
        )
        if question_type not in TheoryQuizQuestion.QuestionType.values:
            raise ValueError(f"questions[{ordinal}].question_type is invalid")
        prepared = {
            "ordinal": ordinal,
            "question_type": question_type,
            "prompt": _text(
                row.get("prompt"), f"questions[{ordinal}].prompt", required=True
            ),
            "model_answer": "",
            "accept_suitable_answer": False,
            "choices": [],
            "pairs": [],
        }
        if question_type == TheoryQuizQuestion.QuestionType.SINGLE_CHOICE:
            prepared["choices"] = _prepare_choices(row.get("choices"), ordinal)
        elif question_type == TheoryQuizQuestion.QuestionType.OPEN_ANSWER:
            prepared["model_answer"] = _text(
                row.get("model_answer"),
                f"questions[{ordinal}].model_answer",
                required=True,
            )
            prepared["accept_suitable_answer"] = _boolean(
                row.get("accept_suitable_answer"),
                f"questions[{ordinal}].accept_suitable_answer",
                False,
            )
        else:
            prepared["pairs"] = _prepare_pairs(row.get("pairs"), ordinal)
        result.append(prepared)
    return result


def _prepare_mind_race_prompts(value) -> list[dict]:
    result = []
    for ordinal, row in _ordinal_rows(value, "prompts"):
        _reject_unknown(row, {"ordinal", "sentence", "missing_text"}, f"prompts[{ordinal}]")
        sentence = _text(row.get("sentence"), f"prompts[{ordinal}].sentence", maximum=2_000, required=True)
        missing_text = _text(
            row.get("missing_text"), f"prompts[{ordinal}].missing_text", maximum=200, required=True
        )
        if not re.search(re.escape(missing_text), sentence, flags=re.IGNORECASE):
            raise ValueError(f"prompts[{ordinal}].missing_text must occur in sentence")
        result.append({"ordinal": ordinal, "sentence": sentence, "missing_text": missing_text})
    return result


def _wonder_answer(value, field_name: str) -> str:
    answer = unicodedata.normalize("NFC", _text(value, field_name, maximum=200, required=True)).upper()
    answer = " ".join(answer.split())
    invalid = [
        character
        for character in answer
        if character != " " and unicodedata.category(character)[:1] not in {"L", "N", "P", "S"}
    ]
    if invalid:
        raise ValueError(f"{field_name} contains unsupported characters")
    return answer


def _prepare_wonder_questions(value) -> list[dict]:
    result = []
    for ordinal, row in _ordinal_rows(value, "questions"):
        _reject_unknown(row, {"ordinal", "prompt", "answer"}, f"questions[{ordinal}]")
        result.append(
            {
                "ordinal": ordinal,
                "prompt": _text(
                    row.get("prompt"), f"questions[{ordinal}].prompt", maximum=3_000, required=True
                ),
                "answer": _wonder_answer(row.get("answer"), f"questions[{ordinal}].answer"),
            }
        )
    return result


def _prepare_tournament_stages(value) -> list[dict]:
    result = []
    for ordinal, row in _ordinal_rows(value, "stages"):
        _reject_unknown(
            row, {"ordinal", "title", "question_count", "questions"}, f"stages[{ordinal}]"
        )
        count = _positive_int(
            row.get("question_count", 3), f"stages[{ordinal}].question_count", maximum=19
        )
        if count % 2 == 0:
            raise ValueError(f"stages[{ordinal}].question_count must be odd")
        questions = []
        for question_ordinal, question in _ordinal_rows(
            row.get("questions"), f"stages[{ordinal}].questions"
        ):
            _reject_unknown(
                question,
                {"ordinal", "prompt", "answer"},
                f"stages[{ordinal}].questions[{question_ordinal}]",
            )
            questions.append(
                {
                    "ordinal": question_ordinal,
                    "prompt": _text(
                        question.get("prompt"),
                        f"stages[{ordinal}].questions[{question_ordinal}].prompt",
                        required=True,
                    ),
                    "answer": _text(
                        question.get("answer"),
                        f"stages[{ordinal}].questions[{question_ordinal}].answer",
                        maximum=300,
                        required=True,
                    ),
                }
            )
        if len(questions) > count:
            raise ValueError(f"stages[{ordinal}] has more questions than question_count")
        result.append(
            {
                "ordinal": ordinal,
                "title": _text(row.get("title"), f"stages[{ordinal}].title", maximum=120),
                "question_count": count,
                "questions": questions,
            }
        )
    return result


def _prepare_module(raw, index: int, used_positions: set[int]) -> dict:
    row = _object(raw, f"modules[{index}]")
    common_fields = {"module_type", "position", "title", "topic", "is_active"}
    module_type = _text(row.get("module_type"), f"modules[{index}].module_type", required=True)
    if module_type not in {"coding_task", "theory_material", "theory_quiz", "game"}:
        raise ValueError(f"modules[{index}].module_type is invalid")
    position = _module_position(row.get("position"), used_positions, f"modules[{index}].position")
    prepared = {
        "module_type": module_type,
        "position": position,
        "title": _text(row.get("title"), f"modules[{index}].title", maximum=200, required=True),
        "topic": _text(row.get("topic"), f"modules[{index}].topic", maximum=255),
        "is_active": _boolean(row.get("is_active"), f"modules[{index}].is_active", True),
    }

    if module_type == "coding_task":
        _reject_unknown(
            row,
            common_fields
            | {"statement", "constraints", "programming_language", "hints", "testcases", "fragments"},
            f"modules[{index}]",
        )
        language = _text(
            row.get("programming_language", SessionTask.ProgrammingLanguage.PYTHON),
            f"modules[{index}].programming_language",
        )
        if language not in SessionTask.ProgrammingLanguage.values:
            raise ValueError(f"modules[{index}].programming_language must be python or cpp")
        prepared.update(
            {
                "statement": _text(
                    row.get("statement"), f"modules[{index}].statement", required=True, strip=False
                ),
                "constraints": _text(
                    row.get("constraints"), f"modules[{index}].constraints", strip=False
                ),
                "programming_language": language,
                "hints": _prepare_hints(row.get("hints")),
                "testcases": _prepare_testcases(row.get("testcases")),
                "fragments": _prepare_fragments(row.get("fragments")),
            }
        )
    elif module_type == "theory_material":
        _reject_unknown(row, common_fields | {"ai_prompt", "blocks"}, f"modules[{index}]")
        prepared.update(
            {
                "ai_prompt": _text(row.get("ai_prompt"), f"modules[{index}].ai_prompt"),
                "blocks": _prepare_theory_blocks(row.get("blocks")),
            }
        )
    elif module_type == "theory_quiz":
        _reject_unknown(row, common_fields | {"instructions", "questions"}, f"modules[{index}]")
        prepared.update(
            {
                "instructions": _text(
                    row.get("instructions"), f"modules[{index}].instructions", strip=False
                ),
                "questions": _prepare_quiz_questions(row.get("questions")),
            }
        )
    else:
        _reject_unknown(
            row,
            common_fields | {"rubric", "prompts", "questions", "stages"},
            f"modules[{index}]",
        )
        rubric = _text(
            row.get("rubric", GameModule.Rubric.MIND_RACE), f"modules[{index}].rubric"
        )
        if rubric not in GameModule.Rubric.values:
            raise ValueError(f"modules[{index}].rubric is invalid")
        prepared["rubric"] = rubric
        if rubric == GameModule.Rubric.MIND_RACE:
            prepared["prompts"] = _prepare_mind_race_prompts(row.get("prompts"))
            if row.get("questions") is not None or row.get("stages") is not None:
                raise ValueError(f"modules[{index}] mind_race accepts prompts only")
        elif rubric == GameModule.Rubric.WONDER_FIELD:
            prepared["questions"] = _prepare_wonder_questions(row.get("questions"))
            if row.get("prompts") is not None or row.get("stages") is not None:
                raise ValueError(f"modules[{index}] wonder_field accepts questions only")
        else:
            prepared["stages"] = _prepare_tournament_stages(row.get("stages"))
            if row.get("prompts") is not None or row.get("questions") is not None:
                raise ValueError(f"modules[{index}] tournament accepts stages only")
    return prepared


def _create_module(session: Session, prepared: dict) -> dict:
    module_type = prepared["module_type"]
    common = {
        "session": session,
        "position": prepared["position"],
        "title": prepared["title"],
    }
    if module_type == "coding_task":
        task = SessionTask.objects.create(
            **common,
            statement=prepared["statement"],
            constraints=prepared["constraints"],
            programming_language=prepared["programming_language"],
            **prepared["hints"],
        )
        TaskTestCase.objects.bulk_create(
            [TaskTestCase(task=task, **row) for row in prepared["testcases"]]
        )
        TaskCodeFragment.objects.bulk_create(
            [TaskCodeFragment(task=task, **row) for row in prepared["fragments"]]
        )
        entity = task
    elif module_type == "theory_material":
        module = TheoryMaterialModule.objects.create(
            **common,
            topic=prepared["topic"],
            ai_prompt=prepared["ai_prompt"],
            is_active=prepared["is_active"],
        )
        TheoryMaterialBlock.objects.bulk_create(
            [TheoryMaterialBlock(module=module, **row) for row in prepared["blocks"]]
        )
        entity = module
    elif module_type == "theory_quiz":
        module = TheoryQuizModule.objects.create(
            **common,
            topic=prepared["topic"],
            instructions=prepared["instructions"],
            is_active=prepared["is_active"],
        )
        for row in prepared["questions"]:
            question = TheoryQuizQuestion.objects.create(
                module=module,
                ordinal=row["ordinal"],
                question_type=row["question_type"],
                prompt=row["prompt"],
                model_answer=row["model_answer"],
                accept_suitable_answer=row["accept_suitable_answer"],
            )
            TheoryQuizChoice.objects.bulk_create(
                [TheoryQuizChoice(question=question, **choice) for choice in row["choices"]]
            )
            TheoryQuizMatchPair.objects.bulk_create(
                [TheoryQuizMatchPair(question=question, **pair) for pair in row["pairs"]]
            )
        entity = module
    else:
        module = GameModule.objects.create(
            **common,
            topic=prepared["topic"],
            rubric=prepared["rubric"],
            is_active=prepared["is_active"],
        )
        if prepared["rubric"] == GameModule.Rubric.MIND_RACE:
            MindRacePrompt.objects.bulk_create(
                [MindRacePrompt(module=module, **row) for row in prepared["prompts"]]
            )
        elif prepared["rubric"] == GameModule.Rubric.WONDER_FIELD:
            WonderFieldQuestion.objects.bulk_create(
                [WonderFieldQuestion(module=module, **row) for row in prepared["questions"]]
            )
        else:
            for row in prepared["stages"]:
                stage = TournamentStage.objects.create(
                    module=module,
                    ordinal=row["ordinal"],
                    title=row["title"],
                    question_count=row["question_count"],
                )
                TournamentQuestion.objects.bulk_create(
                    [TournamentQuestion(stage=stage, **question) for question in row["questions"]]
                )
        entity = module
    return {
        "id": entity.id,
        "module_type": module_type,
        "position": entity.position,
        "title": entity.title,
    }


@require_POST
def teacher_session_modules_import_api(request: HttpRequest, session_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    try:
        data = _json_body(request)
        _reject_unknown(data, {"action", "modules"}, "root")
        if data.get("action") != "create_modules":
            raise ValueError("action must be create_modules")
        raw_modules = _array(data.get("modules"), "modules", maximum=MAX_MODULES)
        if not raw_modules:
            raise ValueError("modules must contain at least one module")

        with transaction.atomic():
            session = get_object_or_404(
                Session.objects.select_for_update(), id=session_id, author=teacher
            )
            used_positions = _existing_positions(session)
            prepared_modules = [
                _prepare_module(raw, index, used_positions)
                for index, raw in enumerate(raw_modules)
            ]
            created = [_create_module(session, prepared) for prepared in prepared_modules]
        return JsonResponse({"ok": True, "created_count": len(created), "modules": created})
    except Http404:
        return _api_error("session not found", 404)
    except ValueError as exc:
        return _api_error(str(exc), 400)
    except IntegrityError:
        logger.exception("Module JSON import conflicted with existing data")
        return _api_error("module data conflicts with existing records", 409)
    except Exception:
        logger.exception("Module JSON import failed")
        return _api_error("failed to process module JSON", 500)
