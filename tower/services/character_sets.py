"""Once-only assembly entitlements and atomic fulfilment, including old collections."""
from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Student
from tower.models import (
    CharacterSetEntitlement, CharacterSetPublication, CosmeticDefinition, PlayerCharacterSet,
    PlayerCosmetic, PlayerHero, TowerAuditEvent,
)


@transaction.atomic
def grant_character_set(*, player, hero, operation=None):
    # Same first lock as pack/exchange/wallet: serializes every writer for a learner.
    Student.objects.select_for_update().get(pk=player.student_id)
    card = PlayerHero.objects.filter(player=player, hero=hero, assembled_at__isnull=False).first()
    if card is None or card.fragments < hero.required_fragments:
        raise ValidationError("A character must be completely assembled before awarding its set.")
    if operation is not None and operation.player_id != player.pk:
        raise ValidationError("The originating operation belongs to another player.")
    entitlement, entitled_now = CharacterSetEntitlement.objects.get_or_create(player=player, hero=hero,
        defaults={"operation": operation, "earned_at": card.assembled_at})
    existing = PlayerCharacterSet.objects.filter(entitlement=entitlement).first()
    if existing:
        return {"hero_id": hero.pk, "entitled": True, "granted": True, "newly_granted": False,
                "version": existing.version.slug, "items": list(existing.items.order_by("cosmetic_id").values_list("cosmetic_id", flat=True))}
    publication = CharacterSetPublication.objects.select_related("version").filter(hero=hero, is_active=True).first()
    if publication is None:
        if entitled_now:
            TowerAuditEvent.objects.create(event_type="character_set_pending", student_id=player.student_id,
                metadata={"hero": hero.pk, "entitlement": entitlement.pk})
        return {"hero_id": hero.pk, "entitled": True, "granted": False, "newly_granted": False, "items": []}
    version = publication.version
    if version.hero_id != hero.pk:
        raise ValidationError("The published set belongs to another character.")
    components = version.config["components"]
    definitions = {item.pk: item for item in CosmeticDefinition.objects.filter(
        pk__in=[item["id"] for item in components.values()]).select_related("set_version")}
    for slot, component in components.items():
        definition = definitions.get(component["id"])
        if (definition is None or definition.acquisition != "character_set" or definition.slot != slot
                or definition.set_version.hero_id != hero.pk):
            raise ValidationError("Published set components are incomplete or inconsistent.")
    grant = PlayerCharacterSet.objects.create(entitlement=entitlement, version=version)
    for component in components.values():
        PlayerCosmetic.objects.create(player=player, cosmetic_id=component["id"], source="character_set", character_set=grant)
    ids = sorted(item["id"] for item in components.values())
    TowerAuditEvent.objects.create(event_type="character_set_grant", student_id=player.student_id,
        metadata={"hero": hero.pk, "version": version.slug, "entitlement": entitlement.pk, "items": ids})
    return {"hero_id": hero.pk, "entitled": True, "granted": True, "newly_granted": True, "version": version.slug, "items": ids}


def restore_character_sets(*, hero_ids=None):
    rows = PlayerHero.objects.filter(assembled_at__isnull=False).select_related("hero", "player")
    if hero_ids is not None:
        rows = rows.filter(hero_id__in=hero_ids)
    totals = {"checked": 0, "granted": 0, "pending": 0}
    for card in rows.order_by("player__student_id", "hero_id").iterator(chunk_size=250):
        result = grant_character_set(player=card.player, hero=card.hero)
        totals["checked"] += 1
        totals["granted"] += int(result["newly_granted"])
        totals["pending"] += int(not result["granted"])
    return totals
