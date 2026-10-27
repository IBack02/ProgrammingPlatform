"""Upload only manifest-approved public artwork; never user or application data."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tower.asset_delivery import asset_key, public_base_url, validated_asset_path
from tower.character_assets import inspect_asset, inspect_set
from tower.ui_corners import corner_assets
from tower.character_contracts import load_character_sets


def upload_inventory():
    paths = set()
    for entry in load_character_sets().published_sets():
        inspect_set(entry)
        paths.update(component.asset.path for component in entry.components.values())
    cards = json.loads((Path(__file__).resolve().parents[2] / "content/cards.json").read_text(encoding="utf-8"))
    paths.update(rarity["frame"].removeprefix("tower/") for rarity in cards["rarities"].values())
    for asset in corner_assets().values():
        inspect_asset(asset)
        paths.add(asset.path)
    return [{"path": path, "local": validated_asset_path(path), "key": asset_key(path),
             "sha256": hashlib.sha256(validated_asset_path(path).read_bytes()).hexdigest(),
             "content_type": {".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml"}[Path(path).suffix]} for path in sorted(paths)]


def verify_public(row, base, session):
    with session.get(f"{base}/{quote(row['key'], safe='/')}", timeout=30, stream=True) as response:
        response.raise_for_status()
        digest, size = hashlib.sha256(), 0
        expected = row["local"].stat().st_size
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > expected:
                raise ValueError("Public file size differs")
            digest.update(chunk)
        if size != expected or digest.hexdigest() != row["sha256"]:
            raise ValueError("Public file hash differs")


class Command(BaseCommand):
    help = "Publish all released set images and frames to R2, then verify their public bytes. Default: dry run."

    def add_arguments(self, parser):
        parser.add_argument("--upload", action="store_true")
        parser.add_argument("--verify-only", action="store_true")
        parser.add_argument("--public-base", default=None)

    def handle(self, *args, **options):
        if options["upload"] and options["verify_only"]:
            raise CommandError("Choose --upload or --verify-only.")
        rows = upload_inventory()
        base = options["public_base"] or settings.TOWER_SET_ASSET_BASE_URL or settings.TOWER_ASSET_BASE_URL
        try:
            base = public_base_url(base)
        except Exception as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"Public image inventory: {len(rows)} files, {sum(row['local'].stat().st_size for row in rows)} bytes.")
        if not options["upload"] and not options["verify_only"]:
            self.stdout.write("Dry run: no upload. Use --upload after configuring R2 credentials.")
            return
        if options["upload"]:
            required = ("R2_ACCOUNT_ID", "R2_BUCKET_NAME", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
            missing = [name for name in required if not os.environ.get(name)]
            if missing:
                raise CommandError("Missing environment variables: " + ", ".join(missing))
            account = os.environ["R2_ACCOUNT_ID"]
            if len(account) != 32 or any(char not in "0123456789abcdef" for char in account.lower()):
                raise CommandError("R2_ACCOUNT_ID must be a 32-character account ID.")
            import boto3
            from botocore.config import Config
            client = boto3.client("s3", endpoint_url=f"https://{account}.r2.cloudflarestorage.com", region_name="auto",
                aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2}))
            for row in rows:
                try:
                    client.put_object(Bucket=os.environ["R2_BUCKET_NAME"], Key=row["key"], Body=row["local"].read_bytes(),
                        ContentType=row["content_type"], CacheControl="public, max-age=31536000, immutable",
                        Metadata={"sha256": row["sha256"]})
                except Exception:
                    raise CommandError(f"R2 upload failed: {row['path']}. Check bucket and credentials.") from None
                self.stdout.write("Uploaded: " + row["path"])
        with requests.Session() as session:
            for row in rows:
                try:
                    verify_public(row, base, session)
                except Exception:
                    raise CommandError(f"Public verification failed: {row['path']}. CDN configuration has not been changed.") from None
        self.stdout.write(self.style.SUCCESS(f"Verified {len(rows)} public images by SHA-256. Set TOWER_SET_ASSET_BASE_URL={base}"))
