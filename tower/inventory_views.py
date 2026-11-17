"""The class-scoped inventory page linked from the reward roster."""
import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from core.ui_translations import UI_TRANSLATIONS, get_ui_lang
from .catalog import presentation_catalog
from .forms import InventoryBalanceForm, InventoryGrantForm, InventoryRemovalForm
from .models import HeroDefinition, PlayerCosmetic, PlayerHero
from .permissions import is_tower_admin, tower_teacher_required
from .services.inventory import update_inventory
from .services.wallet import WalletError
from .wallet_views import _scope, wallet_errors


@require_http_methods(["GET", "POST"])
@tower_teacher_required
@ensure_csrf_cookie
@wallet_errors
def teacher_inventory(request, student_id):
    group, students = _scope(request, request.GET.get("class_id"))
    student = get_object_or_404(students.select_related("tower_player__wallet"), pk=student_id)
    language = get_ui_lang(request)
    text = UI_TRANSLATIONS[language]
    player = getattr(student, "tower_player", None)
    wallet = getattr(player, "wallet", None)
    coins, shards = (wallet.niscoin, wallet.universal_shards) if wallet else (0, 0)
    owned = list(PlayerHero.objects.filter(player=player).select_related("hero").order_by("hero__name")) if player else []
    progress = {card.hero_id: card.fragments for card in owned}
    card_info = {card["id"]: card for card in presentation_catalog(language)["cards"]}
    hero_choices = [("", text["journal_inventory_choose_hero"])]
    for hero in HeroDefinition.objects.filter(is_active=True).order_by("name"):
        hero_choices.append((hero.pk, f"{hero.name} · {progress.get(hero.pk, 0)}/{hero.required_fragments}"))

    url = reverse("tower:teacher_inventory", args=[student_id]) + "?" + urlencode({"class_id": group.pk})
    balance_form = InventoryBalanceForm(initial={
        "niscoin": coins, "universal_shards": shards, "previous_niscoin": coins,
        "previous_shards": shards, "request_id": uuid.uuid4(),
    })
    grant_form = InventoryGrantForm(heroes=hero_choices, initial={"request_id": uuid.uuid4(), "quantity": 1})
    removal_form = None
    status = 200
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        form = None
        if action == "set_balances":
            form = balance_form = InventoryBalanceForm(request.POST)
        elif action == "grant_fragments":
            form = grant_form = InventoryGrantForm(request.POST, heroes=hero_choices)
        elif action == "remove_hero":
            form = removal_form = InventoryRemovalForm(request.POST)
        if form is not None and form.is_valid():
            fields = dict(form.cleaned_data)
            reason, request_id = fields.pop("reason"), fields.pop("request_id")
            actor = {"admin": request.user} if is_tower_admin(request) else {"teacher": request.tower_teacher}
            try:
                result = update_inventory(
                    student_id=student_id, class_id=group.pk, action=action, fields=fields,
                    reason=reason, request_id=request_id, **actor,
                )
            except WalletError as exc:
                error = text.get(f"journal_{exc.code}") or text.get(f"wallet_error_{exc.code}") or text["wallet_form_invalid"]
                status = exc.status
                if exc.code == "inventory_changed":
                    messages.error(request, error)
                    return redirect(url)
            else:
                notice = "journal_inventory_replayed" if result["replayed"] else "journal_inventory_saved"
                messages.success(request, text[notice])
                return redirect(url)
        else:
            status, error = 400, text["wallet_form_invalid"]

    cards = []
    for card in owned:
        remove = InventoryRemovalForm(initial={
            "card_id": card.pk, "fragments": card.fragments, "request_id": uuid.uuid4(),
        })
        submitted = removal_form is not None and str(card.pk) == removal_form.data.get("card_id")
        cards.append({"row": card, "art": card_info.get(card.hero_id),
                      "remove": removal_form if submitted else remove, "remove_open": submitted})
    cosmetics = [] if not player else [
        {"name": item.cosmetic.labels.get(language, item.cosmetic.pk),
         "slot": text.get(f"journal_slot_{item.cosmetic.slot}", item.cosmetic.slot)}
        for item in PlayerCosmetic.objects.filter(player=player).select_related("cosmetic")
        .order_by("cosmetic__slot", "cosmetic_id")
    ]
    return render(request, "tower/teacher_inventory.html", {
        "active": "tower", "student": student, "group": group, "coins": coins, "shards": shards,
        "inventory_url": url, "balance_form": balance_form, "grant_form": grant_form,
        "has_grant_choices": len(hero_choices) > 1, "cards": cards, "cosmetics": cosmetics,
        "wallet_error": error,
    }, status=status)
