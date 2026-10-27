import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import ClassGroup
from tower.character_assets import inspect_set
from tower.character_contracts import load_behavior_profiles, load_character_sets
from tower.models import CharacterSetPublication, CosmeticDefinition


class Command(BaseCommand):
    help = "Check all companion assets, published definitions and optional pilot class without calling OpenAI."

    def add_arguments(self, parser):
        parser.add_argument("--class-id", type=int)
        parser.add_argument("--require-api-key", action="store_true")

    def handle(self, *args, **options):
        catalog = load_character_sets()
        load_behavior_profiles().check_references(catalog)
        if len(catalog.published_sets()) != 12:
            raise CommandError("All twelve sets must be published.")
        publications = {row.hero_id:row for row in CharacterSetPublication.objects.select_related("version")}
        definitions = {row.pk:row for row in CosmeticDefinition.objects.filter(acquisition="character_set").select_related("set_version")}
        try:
            for entry in catalog.sets:
                inspect_set(entry)
                publication = publications.get(entry.hero_id)
                if not publication or not publication.is_active:
                    raise ValueError("A character-set publication is missing or paused.")
                for slot, item in entry.components.items():
                    definition = definitions.get(item.id)
                    if not definition or definition.slot != slot or definition.set_version.hero_id != entry.hero_id:
                        raise ValueError("A character-set definition is missing or inconsistent.")
        except (OSError,ValueError) as exc:
            raise CommandError(str(exc)) from exc
        if options["class_id"] and not ClassGroup.objects.filter(pk=options["class_id"],tower_enabled=True,is_guest=False,
                owner__is_active=True,owner__can_manage_tower=True).exists():
            raise CommandError("The pilot class does not have collection access.")
        configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
        if options["require_api_key"] and not configured:
            raise CommandError("OPENAI_API_KEY is not configured.")
        self.stdout.write(self.style.SUCCESS(f"Ready: 12 sets, 72 assets and published definitions. "
            f"API key configured={configured}; companion flag={settings.TOWER_COMPANION_ENABLED}; no API calls made."))
