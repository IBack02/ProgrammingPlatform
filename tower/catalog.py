"""Presentation catalogue; portraits and frame bytes never enter the database."""

import json
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static


MANIFEST_PATH = Path(__file__).parent / "content" / "cards.json"


def portrait_url(path):
    base = settings.TOWER_ASSET_BASE_URL.rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https" or not parsed.netloc
        or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise ImproperlyConfigured("TOWER_ASSET_BASE_URL must be a public HTTPS base URL.")
    parts = PurePosixPath(path)
    if parts.is_absolute() or ".." in parts.parts or "\\" in path or not path.startswith("chars/"):
        raise ImproperlyConfigured("Portrait paths must stay inside chars/.")
    return f"{base}/{quote(path, safe='/')}"


def presentation_catalog(language="ru"):
    language = language if language in {"ru", "kk", "en"} else "ru"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    cards = []
    for definition in manifest["cards"]:
        rarity = manifest["rarities"][definition["rarity"]]
        cards.append({
            "id": definition["id"], "name": definition["name"],
            "role": definition["roles"][language],
            "biography": definition.get("bios", {}).get(language, ""),
            "rarity": definition["rarity"], "rarity_label": rarity["labels"][language],
            "stars": range(rarity["stars"]), "star_count": rarity["stars"],
            "portrait_url": portrait_url(definition["portrait"]),
            "frame_url": static(rarity["frame"]),
        })
    rarities = [{"id": key, "label": data["labels"][language], "stars": data["stars"]}
                for key, data in manifest["rarities"].items()]
    return {"cards": cards, "rarities": rarities}
