"""Content-addressed Cloudflare URLs with a local-development fallback."""
import hashlib
from functools import lru_cache
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static

from .character_assets import ASSET_ROOT


def public_base_url(base):
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ImproperlyConfigured("Asset base must be a public HTTPS URL without credentials, query or fragment.")
    return base.rstrip("/")


def validated_asset_path(path):
    if not isinstance(path, str):
        raise ImproperlyConfigured("Asset path must be a string.")
    parts = PurePosixPath(path)
    if (parts.is_absolute() or ".." in parts.parts or "\\" in path
            or not path.startswith(("sets/", "frames/"))
            or parts.suffix not in {".png", ".webp", ".svg"}):
        raise ImproperlyConfigured("Asset must be an image inside sets/ or frames/.")
    local = (ASSET_ROOT / path).resolve()
    if not local.is_relative_to(ASSET_ROOT.resolve()) or not local.is_file():
        raise ImproperlyConfigured("Asset file is missing.")
    return local


@lru_cache(maxsize=1024)
def asset_key(path):
    local = validated_asset_path(path)
    digest = hashlib.sha256(local.read_bytes()).hexdigest()
    return f"tower/{digest}/{path}"


def asset_url(path):
    base = getattr(settings, "TOWER_SET_ASSET_BASE_URL", "")
    if base:
        return f"{public_base_url(base)}/{quote(asset_key(path), safe='/')}"
    validated_asset_path(path)
    return static("tower/" + path)
