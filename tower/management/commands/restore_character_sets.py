from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from tower.services.character_sets import restore_character_sets


class Command(BaseCommand):
    help = "Restore once-only rights and available items for already assembled characters without spending currency."

    def add_arguments(self, parser):
        parser.add_argument("--hero", action="append")

    def handle(self, *args, **options):
        try:
            result = restore_character_sets(hero_ids=options["hero"])
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Character-set rewards: {result}. No wallet entries created."))
