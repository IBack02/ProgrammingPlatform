from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from tower.character_contracts import load_behavior_profiles, load_character_sets
from tower.services.character_sets import restore_character_sets
from tower.services.set_publication import publish_character_set


class Command(BaseCommand):
    help = "Publish complete local sets without overwriting immutable versions; restore earned rewards afterwards."

    def add_arguments(self, parser):
        parser.add_argument("--hero", action="append")

    def handle(self, *args, **options):
        catalog = load_character_sets()
        requested = set(options["hero"] or [entry.hero_id for entry in catalog.published_sets()])
        profiles = {profile.hero_id: profile for profile in load_behavior_profiles().profiles}
        entries = {entry.hero_id: entry for entry in catalog.sets}
        if not requested.issubset(entries):
            raise CommandError("Unknown character requested.")
        try:
            for hero in sorted(requested):
                publish_character_set(entries[hero], profiles[hero])
            # After publication commits: do not retain catalog row locks while locking students.
            result = restore_character_sets(hero_ids=requested)
        except (OSError, ValueError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Published {len(requested)} sets. Restored rewards: {result}."))
