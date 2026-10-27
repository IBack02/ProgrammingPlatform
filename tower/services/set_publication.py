from django.core.exceptions import ValidationError
from django.db import transaction

from tower.character_assets import inspect_set
from tower.character_contracts import CharacterSet
from tower.models import CharacterSetPublication, CharacterSetVersion, CosmeticDefinition, HeroDefinition, TowerAuditEvent


@transaction.atomic
def publish_character_set(entry, behavior):
    if behavior.hero_id != entry.hero_id:
        raise ValidationError("The behavior profile belongs to another character.")
    assets = inspect_set(entry)
    config = CharacterSet.model_validate({**entry.model_dump(), "status": "published"}).model_dump(mode="json")
    profile = behavior.model_dump(mode="json")
    hero = HeroDefinition.objects.select_for_update().get(pk=entry.hero_id)
    version, created = CharacterSetVersion.objects.get_or_create(slug=entry.version_id,
        defaults={"hero": hero, "revision": entry.revision, "config": config, "behavior": profile, "asset_manifest": assets})
    if version.config != config or version.behavior != profile or version.asset_manifest != assets or version.hero_id != hero.pk:
        raise ValidationError("Published sets are immutable. Create a new set revision.")
    publication, new_publication = CharacterSetPublication.objects.get_or_create(hero=hero, defaults={"version": version})
    if publication.version.revision > entry.revision:
        raise ValidationError("Refusing to replace a newer published set with an older revision.")
    before = publication.version.slug
    if publication.version_id != version.pk:
        publication.version = version
        publication.save(update_fields=["version"])
    for component in entry.components.values():
        item, new_item = CosmeticDefinition.objects.get_or_create(slug=component.id, defaults={
            "slot": component.slot, "labels": component.labels.model_dump(), "price": 0,
            "acquisition": "character_set", "set_version": version, "appearance": component.model_dump(mode="json"),
        })
        if item.acquisition != "character_set" or item.slot != component.slot or item.set_version.hero_id != hero.pk:
            raise ValidationError("A set cannot replace an existing purchase or another character's item.")
        if not new_item and item.set_version_id != version.pk:
            item.set_version, item.labels, item.appearance = version, component.labels.model_dump(), component.model_dump(mode="json")
            item.save(update_fields=["set_version", "labels", "appearance"])
    if created or new_publication or before != version.slug:
        TowerAuditEvent.objects.create(event_type="character_set_publish",
            metadata={"hero": hero.pk, "before": None if new_publication else before, "after": version.slug,
                      "placeholder": entry.components["companion"].placeholder})
    return version
