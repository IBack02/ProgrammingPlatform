"""Inspect and package authored set assets without modifying image pixels."""
import hashlib
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image

from tower.character_contracts import load_behavior_profiles, load_character_sets


ASSET_ROOT = Path(__file__).parent / "static" / "tower"
PREVIEW_HEROES = ("nis-coder", "nis-artist")
SVG_TAGS = {"svg", "title", "desc", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}


def inspect_asset(asset, *, root=ASSET_ROOT, require_alpha=False):
    path = (root / asset.path).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing asset: {asset.path}")
    if path.suffix == ".svg":
        content = path.read_text(encoding="utf-8")
        if "<!" in content:
            raise ValueError(f"SVG declarations are not allowed: {asset.path}")
        element = ElementTree.fromstring(content)
        if (element.tag != "{http://www.w3.org/2000/svg}svg"
                or element.attrib.get("viewBox") != f"0 0 {asset.width} {asset.height}"
                or element.attrib.get("width") != str(asset.width)
                or element.attrib.get("height") != str(asset.height)):
            raise ValueError(f"SVG dimensions differ from manifest: {asset.path}")
        for node in element.iter():
            if node.tag.removeprefix("{http://www.w3.org/2000/svg}") not in SVG_TAGS:
                raise ValueError(f"Unsupported SVG element: {asset.path}")
            for key, value in node.attrib.items():
                if key.lower().startswith("on") or key in {"href", "style"} or "url(" in value.lower() or "javascript:" in value.lower():
                    raise ValueError(f"Active SVG content is not allowed: {asset.path}")
    else:
        with Image.open(path) as image:
            image.load()
            content_type = Image.MIME.get(image.format)
            if image.size != (asset.width, asset.height):
                raise ValueError(f"Image dimensions differ from manifest: {asset.path}")
            if require_alpha and ("A" not in image.getbands() or image.getchannel("A").getextrema() != (0, 255)):
                raise ValueError(f"Sprite requires transparent padding and opaque artwork: {asset.path}")
    return {"path": asset.path, "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "content_type": "image/svg+xml" if path.suffix == ".svg" else content_type}


def inspect_set(entry, *, root=ASSET_ROOT):
    return [{**inspect_asset(component.asset, root=root, require_alpha=component.slot in {"head", "prop", "companion"}),
             "slot": component.slot, "placeholder": component.placeholder}
            for component in entry.components.values()]


def release_manifest(hero_ids=PREVIEW_HEROES):
    catalog = load_character_sets()
    profiles = load_behavior_profiles()
    profiles.check_references(catalog)
    requested = set(hero_ids)
    entries = [entry for entry in catalog.sets if entry.hero_id in requested]
    if {entry.hero_id for entry in entries} != requested:
        raise ValueError("Unknown character requested for packaging.")
    return {"schema_version": 1, "sets": [{"hero_id": entry.hero_id, "revision": entry.revision,
            "assets": inspect_set(entry)} for entry in entries]}


def verify_public_assets(base_url, manifest, *, session=None):
    """Explicit bounded GET verification; never treats an upload as successful."""
    from urllib.parse import urlsplit
    import requests

    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Public asset base must be a plain HTTPS URL.")
    client = session or requests.Session()
    for entry in manifest["sets"]:
        for asset in entry["assets"]:
            with client.get(f"{base_url.rstrip('/')}/{asset['path']}", timeout=15, stream=True) as response:
                response.raise_for_status()
                digest = hashlib.sha256()
                total = 0
                for chunk in response.iter_content(65536):
                    total += len(chunk)
                    if total > asset["bytes"]:
                        raise ValueError(f"Public asset size differs: {asset['path']}")
                    digest.update(chunk)
                if total != asset["bytes"] or digest.hexdigest() != asset["sha256"]:
                    raise ValueError(f"Public asset differs from packaged version: {asset['path']}")
    return True
