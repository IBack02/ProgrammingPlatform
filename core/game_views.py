import re
import secrets
import unicodedata
from math import ceil, log2
from datetime import timedelta
from functools import wraps

from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.db.models.deletion import ProtectedError
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .exam_views import _api_error, _json_body, _student, _teacher
from .models import (
    ClassGroup,
    GameModule,
    GameParticipant,
    GameRound,
    GameRoundEvent,
    MindRacePrompt,
    SessionClass,
    SessionTask,
    TheoryMaterialModule,
    TheoryQuizModule,
    TournamentMatch,
    TournamentQuestion,
    TournamentStage,
    WonderFieldQuestion,
)
from .security import request_is_limited


WONDER_FIELD_LATIN = "QWERTYUIOPASDFGHJKLZXCVBNM"
WONDER_FIELD_CYRILLIC = "ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮЁӘҒҚҢӨҰҮҺІ"
WONDER_FIELD_DIGITS = "1234567890"
WONDER_FIELD_SYMBOLS = ".,!?-+*/=:%@#&()[]{}_\\|<>\"'№"
WONDER_FIELD_TURN_SECONDS = 20


def _json_errors(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except Http404:
            return _api_error("resource not found", 404)
        except ProtectedError:
            return _api_error("historical game rounds prevent deletion", 409)
        except IntegrityError:
            return _api_error("database conflict", 409)
        except ValueError as exc:
            return _api_error(str(exc), 400)
        except Exception:
            return _api_error("internal server error", 500)

    return wrapped


def _owned_module(teacher, module_id):
    return get_object_or_404(
        GameModule.objects.select_related("session"),
        id=module_id,
        session__author=teacher,
    )


def _owned_round(teacher, round_id, lock=False):
    queryset = GameRound.objects.select_related("module__session", "class_group")
    if lock:
        queryset = queryset.select_for_update()
    return get_object_or_404(queryset, id=round_id, module__session__author=teacher)


def _student_module(student, module_id):
    return get_object_or_404(
        GameModule.objects.select_related("session"),
        id=module_id,
        is_active=True,
        session__sessionclass__class_group=student.class_group,
    )


def _positive_int(value, field_name, maximum=10000):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")
    return parsed


def _normalize_answer(value):
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.strip().casefold().split())


def _validate_prompt(data, prompt=None):
    sentence = str(data.get("sentence", prompt.sentence if prompt else "") or "").strip()
    missing_text = str(data.get("missing_text", prompt.missing_text if prompt else "") or "").strip()
    if not sentence:
        raise ValueError("sentence is required")
    if len(sentence) > 2000:
        raise ValueError("sentence is too long")
    if not missing_text:
        raise ValueError("missing_text is required")
    if len(missing_text) > 200:
        raise ValueError("missing_text is too long")
    if not re.search(re.escape(missing_text), sentence, flags=re.IGNORECASE):
        raise ValueError("missing_text must occur in the sentence")
    return sentence, missing_text


def _masked_sentence(sentence, missing_text):
    blank = "_" * min(18, max(5, len(missing_text)))
    return re.sub(re.escape(missing_text), blank, sentence, flags=re.IGNORECASE)


def _normalize_wonder_answer(value):
    answer = unicodedata.normalize("NFC", str(value or "")).strip().upper()
    answer = " ".join(answer.split())
    if not answer:
        raise ValueError("answer is required")
    if len(answer) > 200:
        raise ValueError("answer is too long")
    invalid = [character for character in answer if character != " " and not _is_wonder_character(character)]
    if invalid:
        raise ValueError("answer may contain letters, numbers, punctuation, symbols, and spaces")
    return answer


def _is_wonder_character(character):
    return len(character) == 1 and unicodedata.category(character)[:1] in {"L", "N", "P", "S"}


def _wonder_keyboard(answer):
    characters = []

    def append_group(group):
        for character in group:
            if character not in characters:
                characters.append(character)

    answer_characters = [character for character in answer if character != " "]
    if any("LATIN" in unicodedata.name(character, "") for character in answer_characters):
        append_group(WONDER_FIELD_LATIN)
    if any("CYRILLIC" in unicodedata.name(character, "") for character in answer_characters):
        append_group(WONDER_FIELD_CYRILLIC)
    if any(unicodedata.category(character).startswith("N") for character in answer_characters):
        append_group(WONDER_FIELD_DIGITS)
    if any(unicodedata.category(character)[:1] in {"P", "S"} for character in answer_characters):
        append_group(WONDER_FIELD_SYMBOLS)
    append_group(answer_characters)
    return characters


def _validate_wonder_question(data, question=None):
    prompt = str(data.get("prompt", question.prompt if question else "") or "").strip()
    if not prompt:
        raise ValueError("question prompt is required")
    if len(prompt) > 3000:
        raise ValueError("question prompt is too long")
    answer = _normalize_wonder_answer(data.get("answer", question.answer if question else ""))
    return prompt, answer


def _position_taken(session, position, skip_id=None):
    if SessionTask.objects.filter(session=session, position=position).exists():
        return True
    if TheoryMaterialModule.objects.filter(session=session, position=position).exists():
        return True
    if TheoryQuizModule.objects.filter(session=session, position=position).exists():
        return True
    games = GameModule.objects.filter(session=session, position=position)
    if skip_id:
        games = games.exclude(id=skip_id)
    return games.exists()


def _serialize_prompt(prompt):
    return {
        "id": prompt.id,
        "ordinal": prompt.ordinal,
        "sentence": prompt.sentence,
        "missing_text": prompt.missing_text,
        "preview": _masked_sentence(prompt.sentence, prompt.missing_text),
    }


def _serialize_wonder_question(question):
    return {
        "id": question.id,
        "ordinal": question.ordinal,
        "prompt": question.prompt,
        "answer": question.answer,
    }


def _serialize_tournament_question(question, include_answer=True):
    data = {"id": question.id, "ordinal": question.ordinal, "prompt": question.prompt}
    if include_answer:
        data["answer"] = question.answer
    return data


def _serialize_tournament_stage(stage):
    return {
        "id": stage.id,
        "ordinal": stage.ordinal,
        "title": stage.title,
        "question_count": stage.question_count,
        "questions": [
            _serialize_tournament_question(row)
            for row in stage.questions.order_by("ordinal", "id")
        ],
    }


def _validate_tournament_stage(data, stage=None):
    ordinal = _positive_int(data.get("ordinal", stage.ordinal if stage else 1), "ordinal", 20)
    count = _positive_int(data.get("question_count", stage.question_count if stage else 3), "question_count", 19)
    if count % 2 == 0:
        raise ValueError("question_count must be odd")
    title = str(data.get("title", stage.title if stage else "") or "").strip()[:120]
    return ordinal, count, title


def _validate_tournament_question(data, question=None):
    ordinal = _positive_int(data.get("ordinal", question.ordinal if question else 1), "ordinal", 50)
    prompt = str(data.get("prompt", question.prompt if question else "") or "").strip()
    answer = str(data.get("answer", question.answer if question else "") or "").strip()
    if not prompt or len(prompt) > 3000:
        raise ValueError("prompt must contain between 1 and 3000 characters")
    if not answer or len(answer) > 300:
        raise ValueError("answer must contain between 1 and 300 characters")
    return ordinal, prompt, answer


def _tournament_match_row(match, snapshot):
    stage = snapshot[match.stage_number - 1] if match.stage_number <= len(snapshot) else {}
    questions = stage.get("questions", [])
    current = questions[match.current_question_index] if match.current_question_index < len(questions) else None
    return {
        "id": match.id,
        "stage_number": match.stage_number,
        "stage_title": stage.get("title") or f"Round {match.stage_number}",
        "match_number": match.match_number,
        "status": match.status,
        "player_one": _participant_row(match.player_one) if match.player_one else None,
        "player_two": _participant_row(match.player_two) if match.player_two else None,
        "winner_id": match.winner_id,
        "score_one": match.score_one,
        "score_two": match.score_two,
        "wins_required": (int(stage.get("question_count", 1)) // 2) + 1,
        "current_question_index": match.current_question_index,
        "current_question": {
            "index": match.current_question_index,
            "ordinal": current.get("ordinal", match.current_question_index + 1),
            "prompt": current.get("prompt", ""),
        } if current and match.status == TournamentMatch.Status.RUNNING else None,
    }


def _tournament_round_data(round_obj):
    snapshot = round_obj.prompt_snapshot or []
    matches = list(
        round_obj.tournament_matches.select_related(
            "player_one__student", "player_two__student", "winner__student"
        )
    )
    entrants = round_obj.participants.count()
    stage_sizes = []
    for _stage in snapshot:
        match_count = ceil(entrants / 2) if entrants > 1 else 1
        stage_sizes.append(match_count)
        entrants = match_count
    return {
        "tournament_stages": [
            {
                "number": index + 1,
                "title": stage.get("title") or f"Round {index + 1}",
                "question_count": stage.get("question_count", 0),
                "match_count": stage_sizes[index],
            }
            for index, stage in enumerate(snapshot)
        ],
        "tournament_matches": [_tournament_match_row(row, snapshot) for row in matches],
    }


def _participant_row(participant):
    return {
        "id": participant.id,
        "student_id": participant.student_id,
        "student_name": participant.student.full_name,
        "avatar_shape": participant.avatar_shape,
        "avatar_color": participant.avatar_color,
        "progress": participant.progress,
        "correct_answers": participant.correct_answers,
        "wrong_answers": participant.wrong_answers,
        "finish_place": participant.finish_place,
        "finished_at": participant.finished_at.isoformat() if participant.finished_at else None,
    }


def _wonder_event_row(event):
    participant = event.participant
    return {
        "id": event.id,
        "event_type": event.event_type,
        "question_index": event.question_index,
        "letter": event.letter,
        "student_id": participant.student_id if participant else None,
        "student_name": participant.student.full_name if participant else "",
        "created_at": event.created_at.isoformat(),
    }


def _wonder_round_data(round_obj):
    participants = list(round_obj.participants.select_related("student").all())
    by_student_id = {row.student_id: row for row in participants}
    queue = [by_student_id[student_id] for student_id in round_obj.turn_order if student_id in by_student_id]
    current_participant = queue[round_obj.turn_index % len(queue)] if queue else None
    question_index = round_obj.current_question_index
    current_question = None
    keyboard_characters = []
    if question_index < len(round_obj.prompt_snapshot):
        snapshot = round_obj.prompt_snapshot[question_index]
        answer = snapshot.get("answer", "")
        keyboard_characters = _wonder_keyboard(answer)
        revealed = set(round_obj.revealed_letters or [])
        correct_events = list(
            round_obj.events.filter(
                event_type=GameRoundEvent.EventType.CORRECT,
                question_index=question_index,
            ).select_related("participant__student")
        )
        guessed_by = {}
        for event in correct_events:
            if event.participant:
                guessed_by.setdefault(event.letter, []).append(event.participant.student.full_name)
        cells = []
        for character in answer:
            if character == " ":
                cells.append({"kind": "space"})
            else:
                cells.append({
                    "kind": "letter",
                    "value": character if character in revealed else "",
                    "guessed_by": guessed_by.get(character, []) if character in revealed else [],
                })
        current_question = {
            "question_index": question_index,
            "ordinal": snapshot.get("ordinal", question_index + 1),
            "prompt": snapshot.get("prompt", ""),
            "cells": cells,
        }
    deadline = None
    seconds_left = 0
    if round_obj.status == GameRound.Status.RUNNING and round_obj.turn_started_at:
        deadline_value = round_obj.turn_started_at + timedelta(seconds=WONDER_FIELD_TURN_SECONDS)
        deadline = deadline_value.isoformat()
        seconds_left = max(0, int((deadline_value - timezone.now()).total_seconds() + 0.999))
    events = list(
        round_obj.events.select_related("participant__student").order_by("-created_at", "-id")[:60]
    )
    return {
        "outcome": round_obj.outcome,
        "current_question_index": question_index,
        "current_question": current_question,
        "strikes": round_obj.strikes,
        "max_strikes": 3,
        "keyboard_characters": keyboard_characters,
        "available_letters": [
            character
            for character in keyboard_characters
            if character not in set(round_obj.used_letters or [])
        ],
        "used_letters": round_obj.used_letters or [],
        "turn_order": [_participant_row(row) for row in queue],
        "current_turn_student_id": current_participant.student_id if current_participant else None,
        "turn_deadline": deadline,
        "seconds_left": seconds_left,
        "events": [_wonder_event_row(row) for row in reversed(events)],
    }


def _round_row(round_obj, include_participants=True):
    data = {
        "id": round_obj.id,
        "rubric": round_obj.module.rubric,
        "run_number": round_obj.run_number,
        "status": round_obj.status,
        "class": {"id": round_obj.class_group_id, "name": round_obj.class_group.name},
        "total_prompts": round_obj.total_prompts,
        "opened_at": round_obj.opened_at.isoformat(),
        "started_at": round_obj.started_at.isoformat() if round_obj.started_at else None,
        "finished_at": round_obj.finished_at.isoformat() if round_obj.finished_at else None,
    }
    if include_participants:
        participants = list(round_obj.participants.select_related("student").all())
        participants.sort(
            key=lambda row: (
                row.finish_place is None,
                row.finish_place or 1_000_000,
                -row.progress,
                row.ready_at,
                row.id,
            )
        )
        data["participants"] = [_participant_row(row) for row in participants]
    if round_obj.module.rubric == GameModule.Rubric.WONDER_FIELD:
        data.update(_wonder_round_data(round_obj))
    elif round_obj.module.rubric == GameModule.Rubric.TOURNAMENT:
        data.update(_tournament_round_data(round_obj))
    return data


def _module_row(module, include_detail=False):
    if module.rubric == GameModule.Rubric.WONDER_FIELD:
        prompt_count = module.wonder_questions.count()
    elif module.rubric == GameModule.Rubric.TOURNAMENT:
        prompt_count = TournamentQuestion.objects.filter(stage__module=module).count()
    else:
        prompt_count = module.prompts.count()
    data = {
        "id": module.id,
        "session_id": module.session_id,
        "position": module.position,
        "title": module.title,
        "topic": module.topic,
        "rubric": module.rubric,
        "is_active": module.is_active,
        "prompt_count": prompt_count,
    }
    if include_detail:
        data["prompts"] = [_serialize_prompt(row) for row in module.prompts.order_by("ordinal", "id")]
        data["wonder_questions"] = [
            _serialize_wonder_question(row)
            for row in module.wonder_questions.order_by("ordinal", "id")
        ]
        data["tournament_stages"] = [
            _serialize_tournament_stage(row)
            for row in module.tournament_stages.prefetch_related("questions").order_by("ordinal", "id")
        ]
        data["classes"] = [
            {"id": row.id, "name": row.name}
            for row in ClassGroup.objects.filter(
                sessionclass__session=module.session,
                owner=module.session.author,
            ).distinct().order_by("name", "id")
        ]
        rounds = (
            module.rounds.select_related("class_group")
            .annotate(participant_count=Count("participants"))
            .order_by("-opened_at", "-id")[:30]
        )
        data["rounds"] = [
            {
                **_round_row(row, include_participants=False),
                "participant_count": row.participant_count,
            }
            for row in rounds
        ]
    return data


def _active_round_for_student(module, student):
    return (
        module.rounds.filter(class_group=student.class_group)
        .select_related("class_group")
        .order_by("-run_number", "-id")
        .first()
    )


def _student_round_state(round_obj, student):
    participant = (
        round_obj.participants.select_related("student")
        .filter(student=student)
        .first()
    )
    data = _round_row(round_obj)
    data["registered"] = bool(participant)
    data["me"] = _participant_row(participant) if participant else None
    data["is_my_turn"] = bool(
        participant
        and round_obj.module.rubric == GameModule.Rubric.WONDER_FIELD
        and data.get("current_turn_student_id") == student.id
        and round_obj.status == GameRound.Status.RUNNING
    )
    data["current_prompt"] = None
    if participant and round_obj.module.rubric == GameModule.Rubric.TOURNAMENT:
        active_match = (
            round_obj.tournament_matches.filter(status=TournamentMatch.Status.RUNNING)
            .filter(Q(player_one=participant) | Q(player_two=participant))
            .order_by("stage_number", "match_number")
            .first()
        )
        data["my_tournament_match_id"] = active_match.id if active_match else None
        data["can_answer_tournament"] = bool(active_match and round_obj.status == GameRound.Status.RUNNING)
        data["tournament_eliminated"] = round_obj.tournament_matches.filter(
            Q(player_one=participant) | Q(player_two=participant),
            status=TournamentMatch.Status.FINISHED,
        ).exclude(winner=participant).exists()
    if (
        participant
        and round_obj.module.rubric == GameModule.Rubric.MIND_RACE
        and round_obj.status == GameRound.Status.RUNNING
        and participant.finish_place is None
        and participant.progress < round_obj.total_prompts
        and participant.progress < len(round_obj.prompt_snapshot)
    ):
        prompt = round_obj.prompt_snapshot[participant.progress]
        data["current_prompt"] = {
            "prompt_index": participant.progress,
            "ordinal": prompt["ordinal"],
            "sentence": prompt["sentence"],
        }
    return data


def _finish_wonder_round(round_obj, outcome, event_type):
    finished_at = timezone.now()
    round_obj.status = GameRound.Status.FINISHED
    round_obj.outcome = outcome
    round_obj.finished_at = finished_at
    round_obj.turn_started_at = None
    round_obj.save(update_fields=["status", "outcome", "finished_at", "turn_started_at"])
    GameRoundEvent.objects.create(
        round=round_obj,
        event_type=event_type,
        question_index=round_obj.current_question_index,
    )


def _advance_wonder_turn(round_obj, now=None):
    if round_obj.turn_order:
        round_obj.turn_index = (round_obj.turn_index + 1) % len(round_obj.turn_order)
    round_obj.turn_started_at = now or timezone.now()


def _apply_wonder_timeout(round_obj):
    if (
        round_obj.module.rubric != GameModule.Rubric.WONDER_FIELD
        or round_obj.status != GameRound.Status.RUNNING
        or not round_obj.turn_started_at
        or not round_obj.turn_order
    ):
        return False
    now = timezone.now()
    if round_obj.turn_started_at + timedelta(seconds=WONDER_FIELD_TURN_SECONDS) > now:
        return False
    student_id = round_obj.turn_order[round_obj.turn_index % len(round_obj.turn_order)]
    participant = round_obj.participants.select_for_update().filter(student_id=student_id).first()
    if participant:
        participant.wrong_answers += 1
        participant.last_answer_at = now
        participant.save(update_fields=["wrong_answers", "last_answer_at"])
    round_obj.strikes += 1
    GameRoundEvent.objects.create(
        round=round_obj,
        participant=participant,
        event_type=GameRoundEvent.EventType.TIMEOUT,
        question_index=round_obj.current_question_index,
    )
    if round_obj.strikes >= 3:
        round_obj.save(update_fields=["strikes"])
        _finish_wonder_round(
            round_obj,
            GameRound.Outcome.LOST,
            GameRoundEvent.EventType.GAME_LOST,
        )
    else:
        _advance_wonder_turn(round_obj, now)
        round_obj.save(update_fields=["strikes", "turn_index", "turn_started_at"])
    return True


def _refresh_wonder_round(round_id):
    with transaction.atomic():
        round_obj = get_object_or_404(
            GameRound.objects.select_for_update().select_related("module", "class_group"),
            id=round_id,
        )
        _apply_wonder_timeout(round_obj)
    return GameRound.objects.select_related("module", "class_group").get(id=round_id)


@_json_errors
@require_http_methods(["GET", "POST"])
def teacher_game_modules_api(request: HttpRequest, session_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    from .models import Session

    session = get_object_or_404(Session, id=session_id, author=teacher)
    if request.method == "GET":
        modules = GameModule.objects.filter(session=session).prefetch_related("prompts", "wonder_questions", "tournament_stages__questions")
        return JsonResponse({"ok": True, "modules": [_module_row(row) for row in modules]})

    data = _json_body(request)
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    position = _positive_int(data.get("position", 1), "position")
    if _position_taken(session, position):
        return _api_error("position is already used by another module", 409)
    rubric = str(data.get("rubric") or GameModule.Rubric.MIND_RACE)
    if rubric not in GameModule.Rubric.values:
        raise ValueError("unsupported game rubric")
    module = GameModule.objects.create(
        session=session,
        position=position,
        title=title[:200],
        topic=str(data.get("topic") or "").strip()[:255],
        rubric=rubric,
    )
    return JsonResponse({"ok": True, "module": _module_row(module, True)}, status=201)


@_json_errors
@require_http_methods(["GET", "PATCH", "DELETE"])
def teacher_game_module_detail_api(request: HttpRequest, module_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    module = _owned_module(teacher, module_id)
    if request.method == "GET":
        return JsonResponse({"ok": True, "module": _module_row(module, True)})
    if request.method == "DELETE":
        module.delete()
        return JsonResponse({"ok": True})

    data = _json_body(request)
    if "title" in data:
        title = str(data.get("title") or "").strip()
        if not title:
            raise ValueError("title is required")
        module.title = title[:200]
    if "topic" in data:
        module.topic = str(data.get("topic") or "").strip()[:255]
    if "rubric" in data:
        rubric = str(data.get("rubric") or "")
        if rubric not in GameModule.Rubric.values:
            raise ValueError("unsupported game rubric")
        if rubric != module.rubric:
            has_content = (
                module.prompts.exists()
                or module.wonder_questions.exists()
                or module.tournament_stages.exists()
            )
            if has_content or module.rounds.exists():
                return _api_error(
                    "delete existing game questions and rounds before changing rubric",
                    409,
                )
            module.rubric = rubric
    if "position" in data:
        position = _positive_int(data["position"], "position")
        if _position_taken(module.session, position, skip_id=module.id):
            return _api_error("position is already used by another module", 409)
        module.position = position
    if "is_active" in data:
        module.is_active = bool(data["is_active"])
    module.save()
    return JsonResponse({"ok": True, "module": _module_row(module, True)})


@_json_errors
@require_http_methods(["GET", "POST"])
def teacher_game_prompts_api(request: HttpRequest, module_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    module = _owned_module(teacher, module_id)
    if module.rubric != GameModule.Rubric.MIND_RACE:
        return _api_error("this endpoint is only available for mind race modules", 409)
    if request.method == "GET":
        return JsonResponse({
            "ok": True,
            "prompts": [_serialize_prompt(row) for row in module.prompts.order_by("ordinal", "id")],
        })
    data = _json_body(request)
    ordinal = _positive_int(data.get("ordinal", 1), "ordinal", 500)
    sentence, missing_text = _validate_prompt(data)
    prompt = MindRacePrompt.objects.create(
        module=module,
        ordinal=ordinal,
        sentence=sentence,
        missing_text=missing_text,
    )
    return JsonResponse({"ok": True, "prompt": _serialize_prompt(prompt)}, status=201)


@_json_errors
@require_http_methods(["PATCH", "DELETE"])
def teacher_game_prompt_detail_api(request: HttpRequest, prompt_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    prompt = get_object_or_404(
        MindRacePrompt.objects.select_related("module__session"),
        id=prompt_id,
        module__session__author=teacher,
    )
    if request.method == "DELETE":
        prompt.delete()
        return JsonResponse({"ok": True})
    data = _json_body(request)
    sentence, missing_text = _validate_prompt(data, prompt)
    if "ordinal" in data:
        prompt.ordinal = _positive_int(data["ordinal"], "ordinal", 500)
    prompt.sentence = sentence
    prompt.missing_text = missing_text
    prompt.save()
    return JsonResponse({"ok": True, "prompt": _serialize_prompt(prompt)})


@_json_errors
@require_http_methods(["GET", "POST"])
def teacher_wonder_questions_api(request: HttpRequest, module_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    module = _owned_module(teacher, module_id)
    if module.rubric != GameModule.Rubric.WONDER_FIELD:
        return _api_error("this endpoint is only available for wonder field modules", 409)
    if request.method == "GET":
        questions = module.wonder_questions.order_by("ordinal", "id")
        return JsonResponse({"ok": True, "questions": [_serialize_wonder_question(row) for row in questions]})
    data = _json_body(request)
    ordinal = _positive_int(data.get("ordinal", 1), "ordinal", 500)
    prompt, answer = _validate_wonder_question(data)
    question = WonderFieldQuestion.objects.create(
        module=module,
        ordinal=ordinal,
        prompt=prompt,
        answer=answer,
    )
    return JsonResponse({"ok": True, "question": _serialize_wonder_question(question)}, status=201)


@_json_errors
@require_POST
def teacher_wonder_questions_shuffle_api(request: HttpRequest, module_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    with transaction.atomic():
        module = get_object_or_404(
            GameModule.objects.select_for_update().select_related("session"),
            id=module_id,
            session__author=teacher,
            rubric=GameModule.Rubric.WONDER_FIELD,
        )
        if module.rounds.filter(status=GameRound.Status.RUNNING).exists():
            return _api_error("questions cannot be shuffled while a game is running", 409)
        questions = list(module.wonder_questions.select_for_update().order_by("ordinal", "id"))
        if len(questions) < 2:
            return JsonResponse({
                "ok": True,
                "questions": [_serialize_wonder_question(row) for row in questions],
            })

        temporary_start = max(row.ordinal for row in questions) + len(questions) + 1
        for offset, question in enumerate(questions):
            question.ordinal = temporary_start + offset
            question.save(update_fields=["ordinal", "updated_at"])

        original_order = [question.id for question in questions]
        secrets.SystemRandom().shuffle(questions)
        if [question.id for question in questions] == original_order:
            questions.append(questions.pop(0))
        for ordinal, question in enumerate(questions, start=1):
            question.ordinal = ordinal
            question.save(update_fields=["ordinal", "updated_at"])

    questions.sort(key=lambda row: (row.ordinal, row.id))
    return JsonResponse({
        "ok": True,
        "questions": [_serialize_wonder_question(row) for row in questions],
    })


@_json_errors
@require_http_methods(["PATCH", "DELETE"])
def teacher_wonder_question_detail_api(request: HttpRequest, question_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    question = get_object_or_404(
        WonderFieldQuestion.objects.select_related("module__session"),
        id=question_id,
        module__session__author=teacher,
        module__rubric=GameModule.Rubric.WONDER_FIELD,
    )
    if request.method == "DELETE":
        question.delete()
        return JsonResponse({"ok": True})
    data = _json_body(request)
    prompt, answer = _validate_wonder_question(data, question)
    if "ordinal" in data:
        question.ordinal = _positive_int(data["ordinal"], "ordinal", 500)
    question.prompt = prompt
    question.answer = answer
    question.save()
    return JsonResponse({"ok": True, "question": _serialize_wonder_question(question)})


@_json_errors
@require_http_methods(["GET", "POST"])
def teacher_tournament_stages_api(request: HttpRequest, module_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    module = _owned_module(teacher, module_id)
    if module.rubric != GameModule.Rubric.TOURNAMENT:
        return _api_error("this endpoint is only available for tournament modules", 409)
    if request.method == "GET":
        stages = module.tournament_stages.prefetch_related("questions").order_by("ordinal", "id")
        return JsonResponse({"ok": True, "stages": [_serialize_tournament_stage(row) for row in stages]})
    ordinal, count, title = _validate_tournament_stage(_json_body(request))
    stage = TournamentStage.objects.create(module=module, ordinal=ordinal, title=title, question_count=count)
    return JsonResponse({"ok": True, "stage": _serialize_tournament_stage(stage)}, status=201)


@_json_errors
@require_http_methods(["PATCH", "DELETE"])
def teacher_tournament_stage_detail_api(request: HttpRequest, stage_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    stage = get_object_or_404(
        TournamentStage.objects.select_related("module__session"),
        id=stage_id,
        module__session__author=teacher,
    )
    if request.method == "DELETE":
        stage.delete()
        return JsonResponse({"ok": True})
    ordinal, count, title = _validate_tournament_stage(_json_body(request), stage)
    stage.ordinal, stage.question_count, stage.title = ordinal, count, title
    stage.save()
    return JsonResponse({"ok": True, "stage": _serialize_tournament_stage(stage)})


@_json_errors
@require_POST
def teacher_tournament_questions_api(request: HttpRequest, stage_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    stage = get_object_or_404(
        TournamentStage.objects.select_related("module__session"),
        id=stage_id,
        module__session__author=teacher,
        module__rubric=GameModule.Rubric.TOURNAMENT,
    )
    ordinal, prompt, answer = _validate_tournament_question(_json_body(request))
    question = TournamentQuestion.objects.create(stage=stage, ordinal=ordinal, prompt=prompt, answer=answer)
    return JsonResponse({"ok": True, "question": _serialize_tournament_question(question)}, status=201)


@_json_errors
@require_http_methods(["PATCH", "DELETE"])
def teacher_tournament_question_detail_api(request: HttpRequest, question_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    question = get_object_or_404(
        TournamentQuestion.objects.select_related("stage__module__session"),
        id=question_id,
        stage__module__session__author=teacher,
    )
    if request.method == "DELETE":
        question.delete()
        return JsonResponse({"ok": True})
    ordinal, prompt, answer = _validate_tournament_question(_json_body(request), question)
    question.ordinal, question.prompt, question.answer = ordinal, prompt, answer
    question.save()
    return JsonResponse({"ok": True, "question": _serialize_tournament_question(question)})


@_json_errors
@require_POST
def teacher_game_open_round_api(request: HttpRequest, module_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    data = _json_body(request)
    class_id = _positive_int(data.get("class_id"), "class_id", 2_000_000_000)
    with transaction.atomic():
        module = get_object_or_404(
            GameModule.objects.select_for_update().select_related("session"),
            id=module_id,
            session__author=teacher,
        )
        class_group = get_object_or_404(
            ClassGroup,
            id=class_id,
            owner=teacher,
            sessionclass__session=module.session,
        )
        existing = module.rounds.filter(
            class_group=class_group,
            status__in=[GameRound.Status.LOBBY, GameRound.Status.RUNNING],
        ).select_related("class_group").first()
        if existing:
            if existing.status == GameRound.Status.RUNNING:
                return _api_error("this class already has a running round", 409)
            return JsonResponse({"ok": True, "round": _round_row(existing)})
        run_number = (
            module.rounds.filter(class_group=class_group).aggregate(value=Max("run_number"))["value"] or 0
        ) + 1
        round_obj = GameRound.objects.create(
            module=module,
            class_group=class_group,
            moderator=teacher,
            run_number=run_number,
        )
    return JsonResponse({"ok": True, "round": _round_row(round_obj)}, status=201)


def _create_tournament_matches(round_obj, stage_number, participants):
    now = timezone.now()
    pairs = []
    players = list(participants)
    for index in range(0, len(players), 2):
        pairs.append((players[index], players[index + 1] if index + 1 < len(players) else None))
    for match_number, (one, two) in enumerate(pairs, start=1):
        is_bye = two is None
        TournamentMatch.objects.create(
            round=round_obj,
            stage_number=stage_number,
            match_number=match_number,
            player_one=one,
            player_two=two,
            winner=one if is_bye else None,
            status=TournamentMatch.Status.FINISHED if is_bye else TournamentMatch.Status.RUNNING,
            started_at=now,
            finished_at=now if is_bye else None,
        )


def _advance_tournament(round_obj):
    while round_obj.status == GameRound.Status.RUNNING:
        latest_stage = (
            round_obj.tournament_matches.aggregate(value=Max("stage_number"))["value"] or 0
        )
        matches = list(
            round_obj.tournament_matches.filter(stage_number=latest_stage).order_by("match_number")
        )
        if not matches or any(row.status != TournamentMatch.Status.FINISHED for row in matches):
            return
        winners = [row.winner for row in matches if row.winner_id]
        if len(winners) == 1:
            winner = winners[0]
            winner.finish_place = 1
            winner.finished_at = timezone.now()
            winner.save(update_fields=["finish_place", "finished_at"])
            round_obj.status = GameRound.Status.FINISHED
            round_obj.outcome = GameRound.Outcome.WON
            round_obj.finished_at = timezone.now()
            round_obj.save(update_fields=["status", "outcome", "finished_at"])
            return
        next_stage = latest_stage + 1
        if next_stage > len(round_obj.prompt_snapshot):
            raise ValueError("the tournament needs another configured round")
        _create_tournament_matches(round_obj, next_stage, winners)


@_json_errors
@require_POST
def teacher_game_start_round_api(request: HttpRequest, round_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    with transaction.atomic():
        round_obj = _owned_round(teacher, round_id, lock=True)
        if round_obj.status != GameRound.Status.LOBBY:
            raise ValueError("only a lobby round can be started")
        if not round_obj.module.session.is_active_now():
            raise ValueError("the lesson session must be running before the game starts")
        if not round_obj.participants.exists():
            raise ValueError("no students are ready")
        if round_obj.module.rubric == GameModule.Rubric.TOURNAMENT:
            participants = list(round_obj.participants.select_for_update().order_by("id"))
            if len(participants) < 2:
                raise ValueError("at least two students must be ready")
            needed_stages = ceil(log2(len(participants)))
            configured_stages = list(
                round_obj.module.tournament_stages.prefetch_related("questions").order_by("ordinal", "id")
            )
            if len(configured_stages) < needed_stages:
                raise ValueError("configure enough tournament rounds for the registered students")
            stages = configured_stages[-needed_stages:]
            snapshot = []
            for stage in stages:
                questions = list(stage.questions.order_by("ordinal", "id"))
                if len(questions) != stage.question_count:
                    raise ValueError("each tournament round must contain exactly its configured question count")
                snapshot.append({
                    "title": stage.title,
                    "question_count": stage.question_count,
                    "questions": [
                        {"ordinal": row.ordinal, "prompt": row.prompt, "answer": row.answer}
                        for row in questions
                    ],
                })
            secrets.SystemRandom().shuffle(participants)
            round_obj.prompt_snapshot = snapshot
            round_obj.total_prompts = sum(row["question_count"] for row in snapshot)
        elif round_obj.module.rubric == GameModule.Rubric.WONDER_FIELD:
            questions = list(round_obj.module.wonder_questions.order_by("ordinal", "id"))
            if not questions:
                raise ValueError("add at least one question before starting")
            round_obj.prompt_snapshot = [
                {"ordinal": row.ordinal, "prompt": row.prompt, "answer": row.answer}
                for row in questions
            ]
            round_obj.total_prompts = len(questions)
            turn_order = list(round_obj.participants.values_list("student_id", flat=True))
            secrets.SystemRandom().shuffle(turn_order)
            round_obj.turn_order = turn_order
            round_obj.turn_index = 0
            round_obj.current_question_index = 0
            round_obj.revealed_letters = []
            round_obj.used_letters = []
            round_obj.strikes = 0
            round_obj.outcome = GameRound.Outcome.PENDING
            round_obj.turn_started_at = timezone.now()
        else:
            prompts = list(round_obj.module.prompts.order_by("ordinal", "id"))
            if not prompts:
                raise ValueError("add at least one sentence before starting")
            round_obj.prompt_snapshot = [
                {
                    "ordinal": prompt.ordinal,
                    "sentence": _masked_sentence(prompt.sentence, prompt.missing_text),
                    "answer": prompt.missing_text,
                }
                for prompt in prompts
            ]
            round_obj.total_prompts = len(prompts)
        round_obj.status = GameRound.Status.RUNNING
        round_obj.started_at = timezone.now()
        round_obj.save(update_fields=[
            "prompt_snapshot",
            "total_prompts",
            "turn_order",
            "turn_index",
            "current_question_index",
            "revealed_letters",
            "used_letters",
            "strikes",
            "outcome",
            "turn_started_at",
            "status",
            "started_at",
        ])
        if round_obj.module.rubric == GameModule.Rubric.TOURNAMENT:
            _create_tournament_matches(round_obj, 1, participants)
            _advance_tournament(round_obj)
    return JsonResponse({"ok": True, "round": _round_row(round_obj)})


@_json_errors
@require_POST
def teacher_game_finish_round_api(request: HttpRequest, round_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    with transaction.atomic():
        round_obj = _owned_round(teacher, round_id, lock=True)
        if round_obj.status == GameRound.Status.FINISHED:
            return JsonResponse({"ok": True, "round": _round_row(round_obj)})
        if round_obj.module.rubric == GameModule.Rubric.WONDER_FIELD:
            _finish_wonder_round(
                round_obj,
                GameRound.Outcome.STOPPED,
                GameRoundEvent.EventType.GAME_STOPPED,
            )
            return JsonResponse({"ok": True, "round": _round_row(round_obj)})
        if round_obj.module.rubric == GameModule.Rubric.TOURNAMENT:
            round_obj.status = GameRound.Status.FINISHED
            round_obj.outcome = GameRound.Outcome.STOPPED
            round_obj.finished_at = timezone.now()
            round_obj.save(update_fields=["status", "outcome", "finished_at"])
            return JsonResponse({"ok": True, "round": _round_row(round_obj)})
        next_place = (
            round_obj.participants.filter(finish_place__isnull=False)
            .aggregate(value=Max("finish_place"))["value"] or 0
        ) + 1
        unfinished = list(
            round_obj.participants.select_for_update()
            .filter(finish_place__isnull=True)
            .order_by("-progress", "wrong_answers", "last_answer_at", "ready_at", "id")
        )
        finished_at = timezone.now()
        for participant in unfinished:
            participant.finish_place = next_place
            participant.finished_at = finished_at
            next_place += 1
        if unfinished:
            GameParticipant.objects.bulk_update(unfinished, ["finish_place", "finished_at"])
        round_obj.status = GameRound.Status.FINISHED
        round_obj.finished_at = finished_at
        round_obj.save(update_fields=["status", "finished_at"])
    return JsonResponse({"ok": True, "round": _round_row(round_obj)})


@_json_errors
@require_GET
def teacher_game_round_state_api(request: HttpRequest, round_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    round_obj = _owned_round(teacher, round_id)
    if round_obj.module.rubric == GameModule.Rubric.WONDER_FIELD:
        round_obj = _refresh_wonder_round(round_obj.id)
    return JsonResponse({"ok": True, "round": _round_row(round_obj)})


@_json_errors
@require_POST
def teacher_game_round_penalty_api(request: HttpRequest, round_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    with transaction.atomic():
        round_obj = _owned_round(teacher, round_id, lock=True)
        if round_obj.module.rubric != GameModule.Rubric.WONDER_FIELD:
            return _api_error("penalties are only available in wonder field", 409)
        _apply_wonder_timeout(round_obj)
        if round_obj.status != GameRound.Status.RUNNING:
            return _api_error("the game is not running", 409)
        round_obj.strikes += 1
        GameRoundEvent.objects.create(
            round=round_obj,
            event_type=GameRoundEvent.EventType.PENALTY,
            question_index=round_obj.current_question_index,
        )
        if round_obj.strikes >= 3:
            round_obj.save(update_fields=["strikes"])
            _finish_wonder_round(
                round_obj,
                GameRound.Outcome.LOST,
                GameRoundEvent.EventType.GAME_LOST,
            )
        else:
            round_obj.save(update_fields=["strikes"])
    return JsonResponse({"ok": True, "round": _round_row(round_obj)})


@_json_errors
@require_POST
def teacher_game_round_remove_penalty_api(request: HttpRequest, round_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    with transaction.atomic():
        round_obj = _owned_round(teacher, round_id, lock=True)
        if round_obj.module.rubric != GameModule.Rubric.WONDER_FIELD:
            return _api_error("penalties are only available in wonder field", 409)
        _apply_wonder_timeout(round_obj)
        if round_obj.status != GameRound.Status.RUNNING:
            return _api_error("the game is not running", 409)
        if round_obj.strikes == 0:
            return JsonResponse({"ok": True, "round": _round_row(round_obj)})
        round_obj.strikes -= 1
        round_obj.save(update_fields=["strikes"])
        GameRoundEvent.objects.create(
            round=round_obj,
            event_type=GameRoundEvent.EventType.PENALTY_REMOVED,
            question_index=round_obj.current_question_index,
        )
    return JsonResponse({"ok": True, "round": _round_row(round_obj)})


@_json_errors
@require_GET
def student_game_module_api(request: HttpRequest, module_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    module = _student_module(student, module_id)
    round_obj = _active_round_for_student(module, student)
    if round_obj and module.rubric == GameModule.Rubric.WONDER_FIELD:
        round_obj = _refresh_wonder_round(round_obj.id)
    return JsonResponse({
        "ok": True,
        "module": {
            "id": module.id,
            "position": module.position,
            "title": module.title,
            "topic": module.topic,
            "rubric": module.rubric,
            "prompt_count": (
                module.wonder_questions.count()
                if module.rubric == GameModule.Rubric.WONDER_FIELD
                else TournamentQuestion.objects.filter(stage__module=module).count()
                if module.rubric == GameModule.Rubric.TOURNAMENT
                else module.prompts.count()
            ),
        },
        "round": _student_round_state(round_obj, student) if round_obj else None,
    })


@_json_errors
@require_POST
def student_game_ready_api(request: HttpRequest, module_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    module = _student_module(student, module_id)
    if not module.session.is_active_now():
        return _api_error("the lesson session is not running", 409)
    if request_is_limited("game_ready", f"{student.id}:{module.id}", limit=20, window_seconds=3600):
        return _api_error("too many ready requests", 429)
    with transaction.atomic():
        round_obj = (
            GameRound.objects.select_for_update()
            .select_related("class_group")
            .filter(
                module=module,
                class_group=student.class_group,
                status=GameRound.Status.LOBBY,
            )
            .order_by("-run_number", "-id")
            .first()
        )
        if not round_obj:
            return _api_error("registration is not open", 409)
        shapes = list(GameParticipant.AvatarShape.values)
        shape = secrets.choice(shapes)
        color = "#{:02x}{:02x}{:02x}".format(
            55 + secrets.randbelow(166),
            55 + secrets.randbelow(166),
            55 + secrets.randbelow(166),
        )
        participant, _ = GameParticipant.objects.get_or_create(
            round=round_obj,
            student=student,
            defaults={"avatar_shape": shape, "avatar_color": color},
        )
    return JsonResponse({
        "ok": True,
        "participant": _participant_row(participant),
        "round": _student_round_state(round_obj, student),
    })


@_json_errors
@require_GET
def student_game_round_state_api(request: HttpRequest, round_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    round_obj = get_object_or_404(
        GameRound.objects.select_related("module", "class_group"),
        id=round_id,
        class_group=student.class_group,
        module__session__sessionclass__class_group=student.class_group,
    )
    if round_obj.module.rubric == GameModule.Rubric.WONDER_FIELD:
        round_obj = _refresh_wonder_round(round_obj.id)
    return JsonResponse({"ok": True, "round": _student_round_state(round_obj, student)})


@_json_errors
@require_POST
def student_game_answer_api(request: HttpRequest, round_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    if request_is_limited(
        "game_answer",
        f"{student.id}:{round_id}",
        limit=180,
        window_seconds=60,
    ):
        return _api_error("answer rate limit exceeded", 429)
    data = _json_body(request)
    answer = str(data.get("answer") or "")
    if not answer.strip() or len(answer) > 200:
        raise ValueError("answer must contain between 1 and 200 characters")
    try:
        prompt_index = int(data.get("prompt_index"))
    except (TypeError, ValueError) as exc:
        raise ValueError("prompt_index must be an integer") from exc

    with transaction.atomic():
        round_obj = get_object_or_404(
            GameRound.objects.select_for_update().select_related("module", "class_group"),
            id=round_id,
            class_group=student.class_group,
        )
        participant = get_object_or_404(
            GameParticipant.objects.select_for_update().select_related("student"),
            round=round_obj,
            student=student,
        )
        if round_obj.status != GameRound.Status.RUNNING:
            return _api_error("the game is not running", 409)
        if round_obj.module.rubric != GameModule.Rubric.MIND_RACE:
            return _api_error("use the letter endpoint for this game rubric", 409)
        if participant.finish_place is not None:
            return JsonResponse({"ok": True, "correct": True, "finished": True, "place": participant.finish_place})
        if prompt_index != participant.progress:
            return JsonResponse({"ok": True, "correct": False, "stale": True, "progress": participant.progress})
        if participant.progress >= len(round_obj.prompt_snapshot):
            return _api_error("prompt sequence is unavailable", 409)

        expected = round_obj.prompt_snapshot[participant.progress]["answer"]
        participant.last_answer_at = timezone.now()
        if _normalize_answer(answer) != _normalize_answer(expected):
            participant.wrong_answers += 1
            participant.save(update_fields=["wrong_answers", "last_answer_at"])
            return JsonResponse({"ok": True, "correct": False, "progress": participant.progress})

        participant.progress += 1
        participant.correct_answers += 1
        finished = participant.progress >= round_obj.total_prompts
        if finished:
            last_place = (
                GameParticipant.objects.filter(round=round_obj, finish_place__isnull=False)
                .aggregate(value=Max("finish_place"))["value"] or 0
            )
            participant.finish_place = last_place + 1
            participant.finished_at = timezone.now()
        participant.save(update_fields=[
            "progress",
            "correct_answers",
            "finish_place",
            "finished_at",
            "last_answer_at",
        ])

    return JsonResponse({
        "ok": True,
        "correct": True,
        "finished": finished,
        "place": participant.finish_place,
        "progress": participant.progress,
    })


@_json_errors
@require_POST
def student_tournament_answer_api(request: HttpRequest, round_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    if request_is_limited("tournament_answer", f"{student.id}:{round_id}", limit=120, window_seconds=60):
        return _api_error("answer rate limit exceeded", 429)
    data = _json_body(request)
    answer = str(data.get("answer") or "")
    if not answer.strip() or len(answer) > 300:
        raise ValueError("answer must contain between 1 and 300 characters")
    match_id = _positive_int(data.get("match_id"), "match_id", 2_000_000_000)
    try:
        question_index = int(data.get("question_index"))
    except (TypeError, ValueError) as exc:
        raise ValueError("question_index must be an integer") from exc

    with transaction.atomic():
        round_obj = get_object_or_404(
            GameRound.objects.select_for_update().select_related("module", "class_group"),
            id=round_id,
            class_group=student.class_group,
            module__rubric=GameModule.Rubric.TOURNAMENT,
        )
        participant = get_object_or_404(
            GameParticipant.objects.select_for_update(), round=round_obj, student=student
        )
        match = get_object_or_404(
            TournamentMatch.objects.select_for_update(),
            id=match_id,
            round=round_obj,
        )
        if round_obj.status != GameRound.Status.RUNNING or match.status != TournamentMatch.Status.RUNNING:
            return _api_error("this tournament match is not running", 409)
        if participant.id not in {match.player_one_id, match.player_two_id}:
            return _api_error("student is not assigned to this match", 403)
        if question_index != match.current_question_index:
            return JsonResponse({"ok": True, "correct": False, "stale": True, "round": _student_round_state(round_obj, student)})
        stage = round_obj.prompt_snapshot[match.stage_number - 1]
        questions = stage.get("questions", [])
        if question_index >= len(questions):
            return _api_error("tournament question is unavailable", 409)
        participant.last_answer_at = timezone.now()
        if _normalize_answer(answer) != _normalize_answer(questions[question_index]["answer"]):
            participant.wrong_answers += 1
            participant.save(update_fields=["wrong_answers", "last_answer_at"])
            return JsonResponse({"ok": True, "correct": False, "round": _student_round_state(round_obj, student)})

        if participant.id == match.player_one_id:
            match.score_one += 1
        else:
            match.score_two += 1
        participant.correct_answers += 1
        participant.last_answer_at = timezone.now()
        participant.progress = max(participant.progress, match.stage_number)
        participant.save(update_fields=["correct_answers", "last_answer_at", "progress"])
        wins_required = (int(stage["question_count"]) // 2) + 1
        won = match.score_one >= wins_required or match.score_two >= wins_required
        if won:
            match.winner = participant
            match.status = TournamentMatch.Status.FINISHED
            match.finished_at = timezone.now()
        else:
            match.current_question_index += 1
        match.save()
        if won:
            _advance_tournament(round_obj)

    return JsonResponse({
        "ok": True,
        "correct": True,
        "match_won": won,
        "round": _student_round_state(round_obj, student),
    })


@_json_errors
@require_POST
def student_game_letter_api(request: HttpRequest, round_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    if request_is_limited(
        "game_letter",
        f"{student.id}:{round_id}",
        limit=40,
        window_seconds=60,
    ):
        return _api_error("letter rate limit exceeded", 429)
    data = _json_body(request)
    letter = unicodedata.normalize("NFC", str(data.get("letter") or "")).strip().upper()
    if not _is_wonder_character(letter):
        raise ValueError("letter must be one valid letter, number, or symbol")
    try:
        question_index = int(data.get("question_index"))
    except (TypeError, ValueError) as exc:
        raise ValueError("question_index must be an integer") from exc

    with transaction.atomic():
        round_obj = get_object_or_404(
            GameRound.objects.select_for_update().select_related("module", "class_group"),
            id=round_id,
            class_group=student.class_group,
        )
        participant = get_object_or_404(
            GameParticipant.objects.select_for_update().select_related("student"),
            round=round_obj,
            student=student,
        )
        if round_obj.module.rubric != GameModule.Rubric.WONDER_FIELD:
            return _api_error("this endpoint is only available for wonder field", 409)
        timed_out = _apply_wonder_timeout(round_obj)
        if round_obj.status != GameRound.Status.RUNNING:
            return _api_error("the game is not running", 409)
        if timed_out:
            return _api_error("turn time expired", 409)
        if question_index != round_obj.current_question_index:
            return JsonResponse({"ok": True, "stale": True, "round": _student_round_state(round_obj, student)})
        if not round_obj.turn_order:
            return _api_error("turn order is unavailable", 409)
        current_student_id = round_obj.turn_order[round_obj.turn_index % len(round_obj.turn_order)]
        if current_student_id != student.id:
            return _api_error("it is not your turn", 409)
        if letter in set(round_obj.used_letters or []):
            return _api_error("this letter has already been used", 409)
        if question_index >= len(round_obj.prompt_snapshot):
            return _api_error("question sequence is unavailable", 409)

        now = timezone.now()
        answer = round_obj.prompt_snapshot[question_index]["answer"]
        if letter not in _wonder_keyboard(answer):
            return _api_error("this character is not available for the current question", 409)
        round_obj.used_letters = [*(round_obj.used_letters or []), letter]
        participant.last_answer_at = now
        correct = letter in answer
        if correct:
            round_obj.revealed_letters = [*(round_obj.revealed_letters or []), letter]
            participant.correct_answers += 1
            participant.progress += 1
            event_type = GameRoundEvent.EventType.CORRECT
        else:
            round_obj.strikes += 1
            participant.wrong_answers += 1
            event_type = GameRoundEvent.EventType.WRONG
        participant.save(update_fields=["progress", "correct_answers", "wrong_answers", "last_answer_at"])
        GameRoundEvent.objects.create(
            round=round_obj,
            participant=participant,
            event_type=event_type,
            question_index=question_index,
            letter=letter,
        )

        question_complete = correct and all(
            character == " " or character in set(round_obj.revealed_letters)
            for character in answer
        )
        if question_complete:
            GameRoundEvent.objects.create(
                round=round_obj,
                participant=participant,
                event_type=GameRoundEvent.EventType.QUESTION_COMPLETE,
                question_index=question_index,
            )
            round_obj.current_question_index += 1
            round_obj.strikes = max(0, round_obj.strikes - 1)
            round_obj.used_letters = []
            round_obj.revealed_letters = []

        if round_obj.strikes >= 3:
            round_obj.save(update_fields=[
                "used_letters", "revealed_letters", "strikes", "current_question_index",
            ])
            _finish_wonder_round(
                round_obj,
                GameRound.Outcome.LOST,
                GameRoundEvent.EventType.GAME_LOST,
            )
        elif round_obj.current_question_index >= round_obj.total_prompts:
            round_obj.save(update_fields=[
                "used_letters", "revealed_letters", "strikes", "current_question_index",
            ])
            _finish_wonder_round(
                round_obj,
                GameRound.Outcome.WON,
                GameRoundEvent.EventType.GAME_WON,
            )
        else:
            _advance_wonder_turn(round_obj, now)
            round_obj.save(update_fields=[
                "used_letters",
                "revealed_letters",
                "strikes",
                "current_question_index",
                "turn_index",
                "turn_started_at",
            ])

    return JsonResponse({
        "ok": True,
        "correct": correct,
        "question_complete": question_complete,
        "round": _student_round_state(round_obj, student),
    })
