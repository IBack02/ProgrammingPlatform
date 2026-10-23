import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tower.catalog import MANIFEST_PATH
from tower.models import CosmeticDefinition, HeroDefinition, PackOffer, PackVersion, TowerAuditEvent


class Command(BaseCommand):
    help = "Publish the bundled, versioned collection catalogue without awarding any items."

    @transaction.atomic
    def handle(self, *args, **options):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        economy = json.loads((Path(MANIFEST_PATH).parent / "economy.json").read_text(encoding="utf-8"))
        for card in manifest["cards"]:
            HeroDefinition.objects.get_or_create(slug=card["id"], defaults={
                "name": card["name"], "rarity": card["rarity"], "roles": card["roles"],
                "portrait": card["portrait"], "required_fragments": 10,
                "shard_cost": economy["shard_costs"][card["rarity"]],
            })
        config = {**economy["pack"], "heroes": {rarity: [card["id"] for card in manifest["cards"] if card["rarity"] == rarity] for rarity in manifest["rarities"]}}
        version, _ = PackVersion.objects.get_or_create(slug=economy["version"], defaults={"config": config})
        if version.config != config:
            raise CommandError("Published versions are immutable. Publish a new version slug.")
        offer, created = PackOffer.objects.get_or_create(slug=economy["offer"], defaults={"version": version})
        if not created and offer.version_id != version.pk:
            before = offer.version.slug
            offer.version = version
            offer.save(update_fields=["version"])
            TowerAuditEvent.objects.create(event_type="catalog_publish",
                metadata={"offer": offer.pk, "before": before, "after": version.slug, "source": "seed_tower_catalog"})
        for item in economy["cosmetics"]:
            CosmeticDefinition.objects.get_or_create(slug=item["slug"], defaults={key: value for key, value in item.items() if key != "slug"})
        self.stdout.write(self.style.SUCCESS("Catalogue ready: 12 heroes, one pack offer and 7 cosmetic items. No player rewards issued."))
