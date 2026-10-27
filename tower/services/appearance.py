"""Owned selections and rendering metadata; trying on never changes ownership."""
from django.db import transaction

from tower.avatar_geometry import place_attachment
from tower.character_contracts import SetComponent, load_avatar_geometry
from tower.models import PlayerCosmetic
from tower.asset_delivery import asset_url
from .collection import COLORS, SHAPES
from .wallet import WalletError, _locked_wallet

AVATAR_SLOTS = ("head", "prop", "glasses", "mustache")
PLATFORM_SLOTS = ("background", "ui_loader", "ui_decoration", "companion")
SELECTION_SLOTS = AVATAR_SLOTS + PLATFORM_SLOTS


def owned_definitions(player):
    # Published revisions may be paused later; permanent acquisitions remain usable.
    return {row.cosmetic_id: row.cosmetic for row in PlayerCosmetic.objects.filter(
        player=player).select_related("cosmetic__set_version__hero")}


@transaction.atomic
def save_appearance(*, student, shape, color, selections, theme):
    wallet = _locked_wallet(student.pk)
    if (not isinstance(shape, str) or shape not in SHAPES or not isinstance(color, str) or color not in COLORS
            or not isinstance(theme, str) or not isinstance(selections, dict)
            or set(selections) != set(SELECTION_SLOTS)
            or any(not isinstance(value, str) for value in selections.values())):
        raise WalletError("invalid_avatar")
    selected = {slot: slug for slot, slug in selections.items() if slug}
    if theme != "midnight":
        selected["theme"] = theme
    owned = owned_definitions(wallet.player)
    if any(slug not in owned or owned[slug].slot != slot for slot, slug in selected.items()):
        raise WalletError("item_not_owned", 403)
    player = wallet.player
    player.avatar_shape, player.avatar_color, player.profile_theme = shape, color, theme
    player.avatar_accessories = {slot: slug for slot, slug in selected.items() if slot in AVATAR_SLOTS}
    player.appearance = {slot: slug for slot, slug in selected.items() if slot in PLATFORM_SLOTS}
    player.save(update_fields=["avatar_shape", "avatar_color", "profile_theme", "avatar_accessories", "appearance"])
    return player


def item_presentation(item, language="en"):
    result = {"id": item.pk, "slot": item.slot, "label": item.labels.get(language, item.pk),
              "hero_id": "", "hero_name": "", "url": "", "placeholder": False}
    if item.acquisition != "character_set":
        return result
    component = SetComponent.model_validate(item.appearance)
    result.update(url=asset_url(component.asset.path), placeholder=component.placeholder,
                  hero_id=item.set_version.hero_id, hero_name=item.set_version.hero.name,
                  animation=component.animation)
    if component.attachment:
        geometry = load_avatar_geometry()
        result["attachment"] = {"url": result["url"], "width": component.asset.width,
            "height": component.asset.height, "layer": component.attachment.layer,
            "matrices": {shape: place_attachment(geometry=geometry, shape=shape, asset=component.asset,
                attachment=component.attachment, size=100).svg_transform() for shape in SHAPES}}
    return result


def appearance_context(player, language="en", *, wardrobe=False):
    definitions = owned_definitions(player)
    selected = {**player.avatar_accessories, **player.appearance}
    items = {slug: item_presentation(item, language) for slug, item in definitions.items()
             if wardrobe or selected.get(item.slot) == slug}
    equipped = {slot: items[slug] for slot, slug in selected.items()
                if slug in items and items[slug]["slot"] == slot}
    attachments = [equipped[slot]["attachment"] for slot in ("head", "prop")
                   if equipped.get(slot, {}).get("attachment")]
    avatar = {"avatar_shape": player.avatar_shape, "avatar_color": player.avatar_color,
              "avatar_accessories": {slot: item["id"] for slot, item in equipped.items() if slot in AVATAR_SLOTS}}
    if attachments:
        avatar["avatar_accessories"]["_set_items"] = attachments
    return {"selected": equipped, "items": list(items.values()), "avatar": avatar,
            "values": {"shape": player.avatar_shape, "color": player.avatar_color,
                       "theme": player.profile_theme, "selections": {slot: equipped.get(slot, {}).get("id", "") for slot in SELECTION_SLOTS}}}
