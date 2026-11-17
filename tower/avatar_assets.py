"""Lightweight delivery copies of the immutable avatar artwork."""
import re
from functools import lru_cache
from urllib.parse import urlsplit

from .asset_delivery import asset_url
from .character_assets import ASSET_ROOT


_ORIGINAL_PATH = re.compile(
    r"(?:^|/)sets/(?P<hero>[a-z0-9-]+)/v[12]/(?P<slot>head|prop)(?:\.[0-9a-f]{12})?\.png$"
)


def avatar_asset_paths():
    return {path.relative_to(ASSET_ROOT).as_posix() for path in (ASSET_ROOT / "sets").glob("*/avatar/*.webp")}


@lru_cache(maxsize=256)
def compact_avatar_path(path):
    match = _ORIGINAL_PATH.search(urlsplit(path).path)
    if match:
        compact = f"sets/{match['hero']}/avatar/{match['slot']}.webp"
        if (ASSET_ROOT / compact).is_file():
            return compact
    return None


def compact_saved_accessories(accessories):
    items = accessories.get("_set_items")
    if not items:
        return accessories
    updated = []
    for item in items:
        path = compact_avatar_path(item["url"])
        # Keep the original SVG coordinate space. Only the raster changes.
        updated.append({**item, "url": asset_url(path)} if path else item)
    return {**accessories, "_set_items": updated}
