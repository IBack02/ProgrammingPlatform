import json
import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from core.ui_translations import UI_TRANSLATIONS, get_ui_lang
from .catalog import presentation_catalog
from .models import (CharacterSetEntitlement, CharacterSetPublication, CosmeticDefinition,
                     HeroDefinition, PackOffer, PlayerCosmetic, PlayerHero)
from .permissions import tower_student_required
from .collection_security import collection_unlocked_required
from .asset_delivery import asset_url
from .services.collection import COLORS, SHAPES, buy_cosmetic, exchange_fragments, open_pack, save_avatar, set_favorite
from .services.wallet import WalletError
from .services.appearance import SELECTION_SLOTS, appearance_context, save_appearance
from .wallet_views import positive_id, student_wallet_context, wallet_errors


def journal_context(request):
    language = get_ui_lang(request)
    text = UI_TRANSLATIONS[language]
    context = student_wallet_context(request)
    wallet = context["wallet"]
    player = wallet.player
    catalog = presentation_catalog(language)
    definitions = {hero.pk: hero for hero in HeroDefinition.objects.filter(is_active=True)}
    progress = {row.hero_id: row for row in PlayerHero.objects.filter(player=player)}
    entitlements = {row.hero_id: row for row in CharacterSetEntitlement.objects.filter(player=player).select_related("fulfilment__version")}
    publications = {row.hero_id: row.version for row in CharacterSetPublication.objects.filter(is_active=True).select_related("version")}
    cards = []
    for card in catalog["cards"]:
        definition = definitions.get(card["id"])
        if not definition:
            continue
        row = progress.get(card["id"])
        fragments = row.fragments if row else 0
        assembled = bool(row and row.assembled_at)
        missing = max(0, definition.required_fragments - fragments)
        entitlement = entitlements.get(card["id"])
        grant = getattr(entitlement, "fulfilment", None)
        set_version = publications.get(card["id"]) or (grant.version if grant else None)
        components = [{"id": component["id"], "label": component["labels"].get(language, component["id"]),
                       "url": asset_url(component["asset"]["path"]), "placeholder": component.get("placeholder", False)}
                      for component in set_version.config["components"].values()] if set_version else []
        cards.append({**card, "fragments": fragments, "required": definition.required_fragments,
            "state": "collected" if assembled else "collecting" if fragments else "undiscovered",
            "collected": assembled, "favorite": bool(row and row.is_favorite),
            "missing": missing, "shard_cost": definition.shard_cost, "completion_cost": missing * definition.shard_cost,
            "set_received": grant is not None, "set_pending": assembled and grant is None, "set_components": components,
            "detail_url": "?" + urlencode({"tab": "collection", "hero": card["id"]}) + "#hero-detail"})
    selected_hero = request.GET.get("hero")
    detail = next((card for card in cards if card["id"] == selected_hero), None)
    if selected_hero and not detail:
        get_object_or_404(HeroDefinition.objects.filter(is_active=True), pk=selected_hero)
    offer = PackOffer.objects.filter(is_active=True).select_related("version").first()
    pack = None
    if offer:
        config = offer.version.config
        pack = {"slug": offer.pk, "version": offer.version.slug, **config,
            "chances": [{"label": rarity["label"], "weight": config["weights"][rarity["id"]], "id": rarity["id"]} for rarity in catalog["rarities"]],
            "remaining": max(0, config["pity_after"] - player.pack_misses),
            "guaranteed": player.pack_misses >= config["pity_after"]}
    openings = player.pack_openings.select_related("version")
    before_opening = positive_id(request.GET.get("openings_before"), optional=True)
    history = list((openings.filter(pk__lt=before_opening) if before_opening else openings)[:11])
    opening_id = positive_id(request.GET.get("opening_id"), optional=True)
    opening = get_object_or_404(openings, pk=opening_id) if opening_id else None
    opening_cards = []
    if opening:
        by_id = {card["id"]: card for card in catalog["cards"]}
        for drop in opening.drops:
            opening_cards.append({"card": by_id.get(drop["hero_id"]), **drop})
    owned = set(PlayerCosmetic.objects.filter(player=player).values_list("cosmetic_id", flat=True))
    items = [{"id": item.pk, "slot": item.slot, "label": item.labels.get(language, item.pk),
              "price": item.price, "owned": item.pk in owned} for item in CosmeticDefinition.objects.filter(is_active=True, acquisition="purchase").order_by("slot", "price", "slug")]
    slots = [{"id": slot, "label": text["journal_slot_" + slot], "selected": player.avatar_accessories.get(slot, ""),
              "items": [item for item in items if item["slot"] == slot and item["owned"]]} for slot in ("head", "glasses", "mustache")]
    tab = request.GET.get("tab", "collection")
    if tab == "appearance":
        tab = "avatar"
    if tab not in ("collection", "packs", "wallet", "avatar"):
        tab = "collection"
    context.update(catalog)
    context.update({"cards": cards, "player": player, "active_tab": tab,
        "collected_count": sum(card["collected"] for card in cards),
        "discovered_count": sum(card["fragments"] > 0 for card in cards),
        "favorite_cards": [card for card in cards if card["favorite"]],
        "hero_detail": detail, "pack": pack, "opening": opening, "opening_cards": opening_cards,
        "recent_openings": history[:10],
        "older_openings_url": "?" + urlencode({"tab": "packs", "openings_before": history[9].pk}) + "#opening-history" if len(history) > 10 else "",
        "cosmetic_items": items, "avatar_slots": slots,
        "avatar_themes": [item for item in items if item["slot"] == "theme" and item["owned"]],
        "avatar_shapes": [{"id": shape, "label": text["journal_shape_" + shape]} for shape in SHAPES],
        "avatar_colors": [{"value": color, "label": text["journal_color_" + str(index)]} for index, color in enumerate(COLORS)], "request_id": uuid.uuid4(),
        "avatar_data": {"avatar_shape": player.avatar_shape, "avatar_color": player.avatar_color, "avatar_accessories": player.avatar_accessories},
        "history_next_url": "?" + urlencode({"tab": "wallet", "before": context["next_before"]}) if context["next_before"] else "",
    })
    wardrobe = appearance_context(player, language, wardrobe=True)
    context["wardrobe"] = wardrobe
    context["appearance_slots"] = [{"id": slot, "label": text["journal_slot_" + slot],
        "selected": wardrobe["values"]["selections"][slot],
        "items": [item for item in wardrobe["items"] if item["slot"] == slot]}
        for slot in SELECTION_SLOTS]
    context["appearance_sets"] = [{"hero_id": row.hero_id, "name": row.hero.name,
        "items": {item["slot"]: item["id"] for item in wardrobe["items"] if item["hero_id"] == row.hero_id}}
        for row in player.set_entitlements.select_related("hero", "fulfilment") if getattr(row, "fulfilment", None)]
    return_to = request.GET.get("section", "avatar")
    context["appearance_section"] = return_to if return_to in ("avatar", "background", "interface", "companion") else "avatar"
    context["try_set"] = request.GET.get("try_set", "")
    context["wardrobe"]["sets"] = context["appearance_sets"]
    context["wardrobe"]["try_set"] = context["try_set"]
    return context


@require_GET
@tower_student_required
@collection_unlocked_required
@ensure_csrf_cookie
@wallet_errors
def journal(request):
    return render(request, "tower/journal.html", journal_context(request))


@require_POST
@tower_student_required
@collection_unlocked_required
@wallet_errors
def student_action(request, action):
    is_json = request.path.startswith("/api/")
    try:
        if is_json:
            try:
                values = json.loads(request.body)
            except (ValueError, UnicodeDecodeError):
                raise WalletError("invalid_json") from None
            if not isinstance(values, dict):
                raise WalletError("invalid_payload")
        else:
            values = request.POST.dict()
            values.pop("csrfmiddlewaretoken", None)
            if action == "exchange":
                values["quantity"] = positive_id(values.get("quantity"))
            if action == "favorite":
                if values.get("favorite") not in ("true", "false"):
                    raise WalletError("invalid_payload")
                values["favorite"] = values["favorite"] == "true"
            if action == "avatar":
                values["accessories"] = {slot: values.pop(slot, "") for slot in ("head", "glasses", "mustache")}
            if action == "appearance":
                values["selections"] = {slot: values.pop(slot, "") for slot in SELECTION_SLOTS}
        schemas = {"pack": {"offer", "version", "request_id"}, "exchange": {"hero_id", "quantity", "request_id"},
                   "cosmetic": {"cosmetic_id", "request_id"}, "favorite": {"hero_id", "favorite"},
                   "avatar": {"shape", "color", "accessories", "theme"},
                   "appearance": {"shape", "color", "selections", "theme"}}
        if action not in schemas or set(values) != schemas[action]:
            raise WalletError("invalid_payload")
        student = request.tower_student
        result, tab = {}, "collection"
        if action == "pack":
            result, tab = open_pack(student=student, **values), "packs"
        elif action == "exchange":
            result = exchange_fragments(student=student, **values)
        elif action == "cosmetic":
            result, tab = buy_cosmetic(student=student, **values), "avatar"
        elif action == "avatar":
            save_avatar(student=student, **values)
            tab = "avatar"
        elif action == "appearance":
            save_appearance(student=student, **values)
            tab = "avatar"
        elif action == "favorite":
            set_favorite(student=student, **values)
    except WalletError as exc:
        if is_json:
            return JsonResponse({"ok": False, "error": exc.code}, status=exc.status)
        context = journal_context(request)
        text = UI_TRANSLATIONS[get_ui_lang(request)]
        context["journal_error"] = text.get("journal_error_" + exc.code, text.get("wallet_error_" + exc.code, text["wallet_form_invalid"]))
        context["active_tab"] = {"pack": "packs", "cosmetic": "avatar", "avatar": "avatar", "appearance": "avatar"}.get(action, "collection")
        if action == "appearance":
            # Preserve valid draft choices after a rejected submission.
            known = {item["id"]: item["slot"] for item in context["wardrobe"]["items"]}
            draft = values.get("selections", {})
            if isinstance(draft, dict):
                for slot in context["appearance_slots"]:
                    chosen = draft.get(slot["id"], "")
                    slot["selected"] = chosen if isinstance(chosen, str) and known.get(chosen) == slot["id"] else ""
            context["wardrobe"]["draft_error"] = True
            from .services.collection import COLORS, SHAPES
            saved = context["wardrobe"]["values"]
            context["wardrobe"]["draft"] = {
                "shape": values.get("shape") if values.get("shape") in SHAPES else saved["shape"],
                "color": values.get("color") if values.get("color") in COLORS else saved["color"],
                "theme": values.get("theme") if values.get("theme") in ["midnight"] + [item["id"] for item in context["avatar_themes"]] else saved["theme"],
                "selections": {slot["id"]: slot["selected"] for slot in context["appearance_slots"]},
            }
        if "request_id" in values:
            context["request_id"] = values["request_id"]
        if action == "exchange":
            context["hero_detail"] = next((card for card in context["cards"] if card["id"] == values.get("hero_id")), None)
        return render(request, "tower/journal.html", context, status=exc.status)
    if is_json:
        return JsonResponse({"ok": True, **result})
    text = UI_TRANSLATIONS[get_ui_lang(request)]
    if action != "pack":
        messages.success(request, text["journal_saved"])
    params = {"tab": tab}
    if action == "pack":
        params["opening_id"] = result["opening_id"]
    return redirect(reverse("tower:home") + "?" + urlencode(params) + ("#pack-result" if action == "pack" else ""))
