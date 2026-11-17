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
    grant = PlayerCharacterSet.objects.select_related("version").filter(entitlement=entitlement).first()
    if grant:
        version = grant.version
        owned = set(grant.items.values_list("cosmetic_id", flat=True))
    else:
        publication = CharacterSetPublication.objects.select_related("version").filter(hero=hero, is_active=True).first()
        if publication is None:
            if entitled_now:
                TowerAuditEvent.objects.create(event_type="character_set_pending", student_id=player.student_id,
                    metadata={"hero": hero.pk, "entitlement": entitlement.pk})
            return {"hero_id": hero.pk, "entitled": True, "granted": False, "newly_granted": False, "items": []}
        version = publication.version
        owned = set()
    if version.hero_id != hero.pk:
        raise ValidationError("The published set belongs to another character.")
    components = version.config["components"]
    missing = {slot: item["id"] for slot, item in components.items() if item["id"] not in owned}
    result = {"hero_id": hero.pk, "entitled": True, "granted": True, "newly_granted": bool(missing),
              "version": version.slug, "items": sorted(owned.union(missing.values()))}
    if not missing:
        return result
    definitions = {item.pk: item for item in CosmeticDefinition.objects.filter(
        pk__in=missing.values()).select_related("set_version")}
    for slot, cosmetic_id in missing.items():
        definition = definitions.get(cosmetic_id)
        if (definition is None or definition.acquisition != "character_set" or definition.slot != slot
                or definition.set_version.hero_id != hero.pk):
            raise ValidationError("Published set components are incomplete or inconsistent.")
    event_type = "character_set_restored" if grant else "character_set_grant"
    if grant is None:
        grant = PlayerCharacterSet.objects.create(entitlement=entitlement, version=version)
    for cosmetic_id in missing.values():
        PlayerCosmetic.objects.create(
            player=player, cosmetic_id=cosmetic_id, source="character_set", character_set=grant,
        )
    TowerAuditEvent.objects.create(event_type=event_type, student_id=player.student_id,
        metadata={"hero": hero.pk, "version": version.slug, "entitlement": entitlement.pk, "items": sorted(missing.values())})
    return result


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
