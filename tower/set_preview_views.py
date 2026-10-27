from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_GET

from tower.avatar_geometry import place_attachment
from tower.catalog import presentation_catalog
from tower.character_assets import PREVIEW_HEROES
from tower.character_contracts import SHAPE_IDS, load_avatar_geometry, load_behavior_profiles, load_character_sets
from tower.character_preview import SHAPE_POINTS
from tower.permissions import tower_teacher_required
from tower.asset_delivery import asset_url
from tower.ui_corners import presentation_asset_path


def preview_context(params):
    entries = {entry.hero_id: entry for entry in load_character_sets().published_sets()}
    profiles = {profile.hero_id: profile for profile in load_behavior_profiles().profiles}
    cards = {card["id"]: card for card in presentation_catalog()["cards"]}
    head_id = params.get("head", PREVIEW_HEROES[0])
    prop_id = params.get("prop", PREVIEW_HEROES[0])
    background_id = params.get("background", PREVIEW_HEROES[0])
    size = int(params.get("size", 96))
    if head_id not in entries or prop_id not in entries or background_id not in entries or size not in (46, 96, 160):
        raise ValueError("Unknown preview selection.")
    geometry = load_avatar_geometry()

    def avatar(shape, head_hero, prop_hero):
        items, anchors = [], []
        for slot, hero in (("head", head_hero), ("prop", prop_hero)):
            component = entries[hero].components[slot]
            placement = place_attachment(geometry=geometry, shape=shape, asset=component.asset,
                                         attachment=component.attachment, size=100)
            items.append({"url": asset_url(component.asset.path), "width": component.asset.width,
                          "height": component.asset.height, "matrix": placement.svg_transform(), "slot": slot})
            anchor = geometry.shapes[shape].anchors[component.attachment.anchor].point
            anchors.append({"x": anchor.x * 100, "y": anchor.y * 100})
        return {"shape": shape, "points": SHAPE_POINTS[shape], "items": items, "anchors": anchors,
                "width": size * 2.4, "height": size * 2.2}

    combinations = [{"label": f"{cards[head]['name']} + {cards[prop]['name']}",
                     "avatars": [avatar(shape, head, prop) for shape in SHAPE_IDS]}
                    for head in PREVIEW_HEROES for prop in PREVIEW_HEROES]
    bundles = []
    for hero, entry in entries.items():
        components = [{"id": slot, "label": item.labels.ru, "url": asset_url(presentation_asset_path(hero, slot, item.asset.path)),
                       "placeholder": item.placeholder} for slot, item in entry.components.items()]
        bundles.append({"hero": hero, "name": cards[hero]["name"], "role": cards[hero]["role"],
                        "components": components, "example": profiles[hero].examples["ru"][0],
                        "tone": profiles[hero].tone})
    return {"bundles": bundles, "head_id": head_id, "prop_id": prop_id, "background_id": background_id,
            "background_url": asset_url(entries[background_id].components["background"].asset.path),
            "size": size, "sizes": (46, 96, 160), "show_anchors": params.get("anchors") == "on",
            "avatars": [avatar(shape, head_id, prop_id) for shape in SHAPE_IDS], "combinations": combinations}


@require_GET
@tower_teacher_required
def set_preview(request):
    try:
        context = preview_context(request.GET)
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid preview selection.")
    return render(request, "tower/set_preview.html", context)
