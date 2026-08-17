import re
import secrets
import unicodedata
from functools import wraps

from django.db import IntegrityError, transaction
from django.db.models import Count, Max
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
    MindRacePrompt,
    SessionClass,
    SessionTask,
    TheoryMaterialModule,
    TheoryQuizModule,
)
from .security import request_is_limited


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


def _round_row(round_obj, include_participants=True):
    data = {
        "id": round_obj.id,
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
    return data


def _module_row(module, include_detail=False):
    data = {
        "id": module.id,
        "session_id": module.session_id,
        "position": module.position,
        "title": module.title,
        "topic": module.topic,
        "rubric": module.rubric,
        "is_active": module.is_active,
        "prompt_count": module.prompts.count(),
    }
    if include_detail:
        data["prompts"] = [_serialize_prompt(row) for row in module.prompts.order_by("ordinal", "id")]
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
    data["current_prompt"] = None
    if (
        participant
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


@_json_errors
@require_http_methods(["GET", "POST"])
def teacher_game_modules_api(request: HttpRequest, session_id: int):
    teacher = _teacher(request)
    if not teacher:
        return _api_error("not authenticated", 401)
    from .models import Session

    session = get_object_or_404(Session, id=session_id, author=teacher)
    if request.method == "GET":
        modules = GameModule.objects.filter(session=session).prefetch_related("prompts")
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
        prompts = list(round_obj.module.prompts.order_by("ordinal", "id"))
        if not prompts:
            raise ValueError("add at least one sentence before starting")
        if not round_obj.participants.exists():
            raise ValueError("no students are ready")
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
        round_obj.save(update_fields=["prompt_snapshot", "total_prompts", "status", "started_at"])
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
    return JsonResponse({"ok": True, "round": _round_row(round_obj)})


@_json_errors
@require_GET
def student_game_module_api(request: HttpRequest, module_id: int):
    student = _student(request)
    if not student:
        return _api_error("not authenticated", 401)
    module = _student_module(student, module_id)
    round_obj = _active_round_for_student(module, student)
    return JsonResponse({
        "ok": True,
        "module": {
            "id": module.id,
            "position": module.position,
            "title": module.title,
            "topic": module.topic,
            "rubric": module.rubric,
            "prompt_count": module.prompts.count(),
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
