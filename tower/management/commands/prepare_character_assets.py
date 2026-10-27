import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from django.core.management.base import BaseCommand, CommandError

from tower.character_assets import ASSET_ROOT, PREVIEW_HEROES, release_manifest, verify_public_assets
from tower.character_contracts import load_character_sets


class Command(BaseCommand):
    help = "Validate pilot art, optionally package original files for R2 or verify a published HTTPS copy."

    def add_arguments(self, parser):
        parser.add_argument("--hero", action="append", choices=None)
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--archive", type=Path)
        parser.add_argument("--verify-public-base")

    def handle(self, *args, **options):
        try:
            heroes = [entry.hero_id for entry in load_character_sets().published_sets()] if options["all"] else options["hero"] or PREVIEW_HEROES
            manifest = release_manifest(heroes)
            if options["verify_public_base"]:
                verify_public_assets(options["verify_public_base"], manifest)
            if options["archive"]:
                destination = options["archive"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
                    for entry in manifest["sets"]:
                        for asset in entry["assets"]:
                            archive.write(ASSET_ROOT / asset["path"], asset["path"])
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            if options["verify_public_base"]:
                raise CommandError(f"Public asset verification failed: {type(exc).__name__}") from exc
            raise
        self.stdout.write(self.style.SUCCESS(
            f"Assets valid: {len(manifest['sets'])} sets, {sum(len(row['assets']) for row in manifest['sets'])} files. "
            "Companion placeholders are explicit. No database records changed."
        ))
