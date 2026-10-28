"""Publish new pack rules without rewriting old versions or opening receipts."""
from copy import deepcopy
from importlib import import_module

from django.db import migrations, models
from django.utils import timezone


def publish_rules(apps, schema_editor):
    alias = schema_editor.connection.alias

    def rows(name):
        return apps.get_model("tower", name).objects.using(alias)

    config = deepcopy(import_module("tower.migrations.0004_seed_initial_catalog").INITIAL_PACK)
    config.update(weights={"common": 70, "rare": 22, "epic": 7, "legendary": 0},
                  legendary_cycle_bps=[60, 109, 158, 207, 256, 304, 353, 402, 451, 500],
                  legendary_fragments=1)
    version, _ = rows("PackVersion").get_or_create(slug="school-v2", defaults={"config": config})
    if version.config != config:
        raise RuntimeError("Published school-v2 pack configuration cannot be overwritten.")
    offer = rows("PackOffer").filter(pk="school", version__slug="school-v1").first()
    if offer:
        offer.version_id = version.pk
        offer.save(using=alias, update_fields=["version"])
        rows("TowerAuditEvent").create(event_type="catalog_publish", metadata={
            "offer": "school", "before": "school-v1", "after": "school-v2", "source": "migration_0012"})

    rows("HeroDefinition").filter(rarity="epic").update(required_fragments=6)
    rows("HeroDefinition").filter(rarity="legendary").update(required_fragments=1)
    publications = {row.hero_id: row.version for row in
                    rows("CharacterSetPublication").filter(is_active=True).select_related("version")}
    cards = rows("PlayerHero").filter(hero__rarity__in=["epic", "legendary"]).select_related("hero", "player")
    for card in cards.iterator(chunk_size=250):
        if card.fragments < card.hero.required_fragments:
            continue
        if card.assembled_at is None:
            card.assembled_at = timezone.now()
            card.save(using=alias, update_fields=["assembled_at"])
            rows("TowerAuditEvent").create(event_type="collection_rules_unlock", student_id=card.player.student_id,
                metadata={"hero": card.hero_id, "fragments": card.fragments,
                          "required": card.hero.required_fragments, "source": "migration_0012"})
        entitlement, _ = rows("CharacterSetEntitlement").get_or_create(
            player_id=card.player_id, hero_id=card.hero_id, defaults={"earned_at": card.assembled_at})
        if rows("PlayerCharacterSet").filter(entitlement_id=entitlement.pk).exists():
            continue
        published = publications.get(card.hero_id)
        if published is None:
            continue
        grant = rows("PlayerCharacterSet").create(entitlement_id=entitlement.pk, version_id=published.pk)
        ids = []
        for component in published.config["components"].values():
            rows("PlayerCosmetic").create(player_id=card.player_id, cosmetic_id=component["id"],
                source="character_set", character_set_id=grant.pk)
            ids.append(component["id"])
        rows("TowerAuditEvent").create(event_type="character_set_grant", student_id=card.player.student_id,
            metadata={"hero": card.hero_id, "version": published.slug,
                      "items": sorted(ids), "source": "migration_0012"})


class Migration(migrations.Migration):
    dependencies = [("tower", "0011_collection_password")]
    operations = [
        migrations.AddField(model_name="towerplayer", name="pack_cycle_position",
                            field=models.PositiveSmallIntegerField(default=0)),
        migrations.RunPython(publish_rules, migrations.RunPython.noop),
    ]
