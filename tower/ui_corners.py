"""Presentation variants preserve published set snapshots and permanent item IDs."""
import json
from functools import lru_cache
from pathlib import Path

from .character_contracts import Asset


@lru_cache(maxsize=1)
def corner_assets():
    data = json.loads((Path(__file__).parent / "content/ui_corners.json").read_text(encoding="utf-8"))
    return {hero: Asset.model_validate(asset) for hero, asset in data["corners"].items()}


def presentation_asset_path(hero_id, slot, fallback):
    corner = corner_assets().get(hero_id) if slot == "ui_decoration" else None
    return corner.path if corner else fallback
