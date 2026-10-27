import json
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from PIL import Image

from tower.character_assets import ASSET_ROOT
from tower.character_contracts import CONTENT_DIR, CharacterSetCatalog, load_character_sets


class Command(BaseCommand):
    help = "Import supplied transparent chibi PNGs unchanged, replacing only temporary placeholders."

    def add_arguments(self, parser):
        parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parents[3] / "docs" / "chibi")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        catalog = load_character_sets()
        data = catalog.model_dump()
        planned = []
        imported = 0
        try:
            for entry in data["sets"]:
                source = options["directory"] / f"{entry['hero_id']}.png"
                if not source.is_file():
                    continue
                with Image.open(source) as image:
                    image.load()
                    if image.format != "PNG" or not 128 <= min(image.size) or max(image.size) > 8192:
                        raise ValueError(f"Expected a PNG between 128 and 8192 px: {source.name}")
                    if "A" not in image.getbands() or image.getchannel("A").getextrema() != (0, 255):
                        raise ValueError(f"Expected transparent padding and visible artwork: {source.name}")
                    width, height = image.size
                component = entry["components"]["companion"]
                current = ASSET_ROOT / component["asset"]["path"]
                if not component["placeholder"] and current.is_file() and current.read_bytes() == source.read_bytes():
                    continue
                if entry["status"] == "published" or (not component["placeholder"] and current.exists()):
                    # Keep every published original and create a new draft revision.
                    entry["revision"] += 1
                    entry["status"] = "draft"
                    for slot, other in entry["components"].items():
                        if slot == "companion":
                            continue
                        original = ASSET_ROOT / other["asset"]["path"]
                        destination = ASSET_ROOT / f"sets/{entry['hero_id']}/v{entry['revision']}/{original.name}"
                        if not original.is_file() or destination.exists():
                            raise ValueError(f"Cannot prepare a new immutable asset revision: {original.name}")
                        planned.append((original, destination))
                        other["asset"]["path"] = destination.relative_to(ASSET_ROOT).as_posix()
                destination = ASSET_ROOT / f"sets/{entry['hero_id']}/v{entry['revision']}/companion.png"
                if destination.exists():
                    if source.read_bytes() != destination.read_bytes():
                        raise ValueError(f"Existing companion differs; create a new revision: {source.name}")
                    continue
                component["asset"] = {"path": destination.relative_to(ASSET_ROOT).as_posix(), "width": width, "height": height}
                component["placeholder"] = False
                planned.append((source, destination))
                imported += 1
            CharacterSetCatalog.model_validate(data)
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        if planned and not options["dry_run"]:
            created = []
            try:
                for source, destination in planned:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    created.append(destination)
                manifest = CONTENT_DIR / "character_sets.json"
                temporary = manifest.with_suffix(".json.new")
                temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                temporary.replace(manifest)
            except OSError:
                for file in created:
                    file.unlink(missing_ok=True)
                raise
        self.stdout.write(self.style.SUCCESS(f"{'Ready to import' if options['dry_run'] else 'Imported'}: {imported} chibi files. No AI calls."))
