import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from tower.catalog import MANIFEST_PATH
from tower.character_assets import inspect_set
from tower.character_contracts import contract_schemas, load_avatar_geometry, load_behavior_profiles, load_character_sets
from tower.character_preview import geometry_preview
from tower.companion_contracts import (
    COOLDOWN, FAILURE_RETRY_DELAY, REPLY_MAX_CHARACTERS, REPLY_SENTENCES, RESERVATION_TTL, SOURCE_PRIORITY,
)
from tower.services.collection import SHAPES


class Command(BaseCommand):
    help = "Validate draft character-set contracts offline, without publishing, granting or calling AI."

    def add_arguments(self, parser):
        parser.add_argument("--schema", action="store_true", help="Print JSON schemas and companion policy.")
        parser.add_argument("--preview", type=Path, help="Save an SVG schematic of the attachment geometry.")
        parser.add_argument("--require-published", action="store_true", help="Fail if any character set is still a draft.")

    def handle(self, *args, **options):
        try:
            catalog = load_character_sets()
            load_behavior_profiles().check_references(catalog)
            geometry = load_avatar_geometry()
            cards = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            economy = json.loads((MANIFEST_PATH.parent / "economy.json").read_text(encoding="utf-8"))
            catalog.check_references((card["id"] for card in cards["cards"]),
                                     (item["slug"] for item in economy["cosmetics"]))
            if set(geometry.shapes) != set(SHAPES):
                raise ValueError("Geometry and the existing avatar renderer have different shape IDs.")
            published = len(catalog.published_sets())
            if options["require_published"] and published != len(catalog.sets):
                raise ValueError("Character sets are drafts; art and behavior profiles are not published yet.")
            if options["require_published"]:
                for entry in catalog.sets:
                    inspect_set(entry)
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        if options["schema"]:
            self.stdout.write(json.dumps({**contract_schemas(), "companion_policy": {
                "scope": "account", "cooldown_seconds": int(COOLDOWN.total_seconds()),
                "reservation_seconds": int(RESERVATION_TTL.total_seconds()),
                "failure_retry_seconds": int(FAILURE_RETRY_DELAY.total_seconds()),
                "cooldown_starts": "validated_response_persisted", "generation_trigger": "explicit_click",
                "source_priority": list(SOURCE_PRIORITY), "sentences": list(REPLY_SENTENCES),
                "max_characters": REPLY_MAX_CHARACTERS,
            }}, ensure_ascii=False, indent=2))
            return
        if options["preview"]:
            preview = options["preview"]
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_text(geometry_preview(geometry), encoding="utf-8")
            self.stdout.write(f"Geometry preview: {preview.resolve()}")
        count = sum(len(entry.components) for entry in catalog.sets)
        self.stdout.write(self.style.SUCCESS(
            f"Contracts valid: {len(catalog.sets)} sets, {count} components, {len(geometry.shapes)} shapes; "
            f"published={published}, drafts={len(catalog.sets) - published}. No items issued."
        ))
