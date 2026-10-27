import json
import uuid
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from core.models import Student
from core.ui_translations import UI_TRANSLATIONS, get_ui_lang
from .forms import AwardForm, BatchAwardForm, RewardPolicyForm
from .models import RewardPolicy, WalletEntry
from .permissions import is_tower_admin, managed_classes, tower_student_required, tower_teacher_required
from .collection_security import collection_unlocked_required
from .services.wallet import WalletError, award_coins, student_wallet
from .services.rewards import award_batch, save_policy


def wallet_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except WalletError as exc:
            if request.path.startswith("/api/"):
                return JsonResponse({"ok": False, "error": exc.code}, status=exc.status)
            return render(request, "tower/denied.html", status=exc.status)
        except (PermissionDenied, Http404) as exc:
            if request.path.startswith("/api/"):
                status = 403 if isinstance(exc, PermissionDenied) else 404
                return JsonResponse({"ok": False, "error": "tower_access_denied" if status == 403 else "not_found"}, status=status)
            raise
    return wrapped


def positive_id(value, *, optional=False):
    if value in (None, "") and optional:
        return None
    if isinstance(value, bool):
        raise WalletError("invalid_identifier")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise WalletError("invalid_identifier") from None
    if str(result) != str(value) or not 0 < result <= 9_223_372_036_854_775_807:
        raise WalletError("invalid_identifier")
    return result


def _history(entries, before):
    cursor = positive_id(before, optional=True)
    if cursor:
        entries = entries.filter(id__lt=cursor)
    rows = list(entries.order_by("-id")[:26])
    return rows[:25], rows[24].pk if len(rows) > 25 else None


def _entry_json(entry):
    return {
        "id": entry.pk, "currency": entry.currency, "delta": entry.delta,
        "balance_after": entry.balance_after, "reason": entry.reason,
        "source": entry.source, "created_at": entry.created_at.isoformat(),
    }


def student_wallet_context(request):
    wallet = student_wallet(request.tower_student)
    entries, next_before = _history(wallet.entries.all(), request.GET.get("before"))
    return {
        "wallet": wallet, "wallet_entries": entries,
        "next_before": next_before,
        "history_next_url": f"?before={next_before}" if next_before else "",
    }


@require_GET
@tower_student_required
@collection_unlocked_required
@wallet_errors
def wallet_api(request):
    context = student_wallet_context(request)
    wallet = context["wallet"]
    return JsonResponse({
        "ok": True,
        "wallet": {"niscoin": wallet.niscoin, "universal_shards": wallet.universal_shards},
        "entries": [_entry_json(entry) for entry in context["wallet_entries"]],
        "next_before": context["next_before"],
    })


def _scope(request, class_id):
    group = get_object_or_404(managed_classes(request), pk=positive_id(class_id))
    if not (group.tower_enabled and not group.is_guest and group.owner
            and group.owner.is_active and group.owner.can_manage_tower):
        raise PermissionDenied
    students = Student.objects.filter(
        Q(class_group=group) | Q(class_memberships__class_group=group),
        is_active=True, class_group__is_guest=False, guest_lease_token__isnull=True,
    ).distinct()
    return group, students


def _teacher_history(request, group, student):
    entries = WalletEntry.objects.filter(wallet__player__student=student, class_group=group).select_related("wallet__player__student")
    if not is_tower_admin(request):
        entries = entries.filter(operation__actor_teacher=request.tower_teacher)
    return _history(entries, request.GET.get("before"))


def _award(request, values):
    return award_coins(
        student_id=positive_id(values["student_id"]), class_id=positive_id(values["class_id"]),
        amount=values["amount"], reason=values["reason"], request_id=values["request_id"],
        admin=request.user if is_tower_admin(request) else None,
        teacher=None if is_tower_admin(request) else request.tower_teacher,
    )


@require_POST
@tower_teacher_required
@wallet_errors
def award_api(request):
    try:
        values = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise WalletError("invalid_json") from None
    if not isinstance(values, dict) or set(values) != {"student_id", "class_id", "amount", "reason", "request_id"}:
        raise WalletError("invalid_payload")
    # JSON identifiers and amounts must be actual integers, never booleans or floats.
    if any(type(values[key]) is not int for key in ["student_id", "class_id", "amount"]):
        raise WalletError("invalid_amount")
    return JsonResponse({"ok": True, **_award(request, values)})


@require_GET
@tower_teacher_required
@wallet_errors
def teacher_history_api(request, student_id):
    group, students = _scope(request, request.GET.get("class_id"))
    student = get_object_or_404(students, pk=student_id)
    entries, next_before = _teacher_history(request, group, student)
    player = getattr(student, "tower_player", None)
    wallet = getattr(player, "wallet", None) if player else None
    return JsonResponse({
        "ok": True, "niscoin": wallet.niscoin if wallet else 0,
        "entries": [_entry_json(entry) for entry in entries], "next_before": next_before,
    })


@require_http_methods(["GET", "POST"])
@tower_teacher_required
@ensure_csrf_cookie
@wallet_errors
def teacher_wallets(request):
    text = UI_TRANSLATIONS[get_ui_lang(request)]
    groups = managed_classes(request).filter(
        tower_enabled=True, is_guest=False, owner__is_active=True, owner__can_manage_tower=True,
    )
    chosen = request.POST.get("class_id") if request.method == "POST" else request.GET.get("class_id")
    if not chosen:
        first = groups.first()
        chosen = first.pk if first else None
    group, students = _scope(request, chosen) if chosen else (None, Student.objects.none())
    bound_form = None
    error = ""
    policy_form = RewardPolicyForm(instance=RewardPolicy.objects.filter(class_group=group).first() if group else None)
    batch_form = None
    action = request.POST.get("action", "single")
    if request.method == "POST" and action in ("policy", "batch"):
        if not group:
            raise Http404
        actor = {"admin": request.user} if is_tower_admin(request) else {"teacher": request.tower_teacher}
        form = (RewardPolicyForm(request.POST, instance=policy_form.instance) if action == "policy"
                else BatchAwardForm(request.POST, students=students))
        if action == "policy":
            policy_form = form
        else:
            batch_form = form
        if form.is_valid():
            try:
                if action == "policy":
                    save_policy(class_id=group.pk, values=form.cleaned_data, **actor)
                    notice = text["reward_policy_saved"]
                else:
                    result = award_batch(**form.cleaned_data, **actor)
                    notice = text["wallet_award_replayed"] if result["replayed"] else text["reward_batch_success"].format(count=result["count"])
            except WalletError as exc:
                error = text.get(f"wallet_error_{exc.code}", text["wallet_form_invalid"])
            else:
                messages.success(request, notice)
                return redirect(f"{reverse('tower:teacher')}?{urlencode({'class_id': group.pk})}")
        else:
            error = text["wallet_form_invalid"]
    elif request.method == "POST":
        bound_form = AwardForm(request.POST)
        if bound_form.is_valid():
            try:
                result = _award(request, bound_form.cleaned_data)
            except WalletError as exc:
                error = text.get(f"wallet_error_{exc.code}", text["wallet_error_invalid_amount"])
            else:
                messages.success(request, text["wallet_award_replayed"] if result["replayed"] else text["wallet_award_success"])
                target = urlencode({"class_id": group.pk, "student_id": bound_form.cleaned_data["student_id"]})
                return redirect(f"{reverse('tower:teacher_wallets')}?{target}")
        else:
            error = text["wallet_form_invalid"]
    query = request.GET.get("q", "").strip()[:120]
    visible_students = students.filter(full_name__icontains=query) if query else students
    student_page = Paginator(visible_students.select_related("tower_player__wallet").order_by("full_name", "id"), 50).get_page(request.GET.get("page"))
    rows = []
    for student in student_page:
        player = getattr(student, "tower_player", None)
        wallet = getattr(player, "wallet", None) if player else None
        rows.append({"student": student, "balance": wallet.niscoin if wallet else 0,
                     "selected": str(student.pk) in request.POST.getlist("student_ids")})
    selected = positive_id(request.GET.get("student_id"), optional=True)
    history_student = get_object_or_404(students, pk=selected) if selected else None
    if history_student:
        entries, next_before = _teacher_history(request, group, history_student)
    else:
        history = WalletEntry.objects.filter(class_group=group).select_related("wallet__player__student") if group else WalletEntry.objects.none()
        if not is_tower_admin(request):
            history = history.filter(operation__actor_teacher=request.tower_teacher)
        entries, next_before = _history(history, request.GET.get("before"))
    from .views import _serialize_class
    return render(request, "tower/teacher_wallets.html", {
        "active": "tower", "wallet_classes": groups, "wallet_class": group,
        "wallet_rows": rows, "student_page": student_page, "wallet_error": error,
        "history_student": history_student, "wallet_entries": entries,
        "tower_classes": [_serialize_class(item) for item in managed_classes(request)],
        "policy_form": policy_form, "reward_query": query,
        "batch_form": batch_form or BatchAwardForm(students=students, initial={"class_id": group.pk if group else "", "request_id": uuid.uuid4()}),
        "history_next_url": "?" + urlencode({"class_id": group.pk, **({"student_id": selected} if selected else {}), "before": next_before}) + "#wallet-history" if next_before else "",
    }, status=400 if error else 200)
