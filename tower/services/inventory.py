"""Teacher corrections to a student's collection and balances."""
import hashlib
import json

from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404

from tower.models import MAX_BALANCE, GameOperation, HeroDefinition, PlayerCosmetic, PlayerHero, TowerAuditEvent, WalletEntry
from tower.permissions import wallet_students
from .collection import _grant
from .rewards import _operator_group
from .wallet import WalletError, _locked_wallet, _positive_amount, _reason, _record_wallet_delta, _request_id


@transaction.atomic
def update_inventory(*, student_id, class_id, action, fields, reason, request_id, teacher=None, admin=None):
    if action not in {"set_balances", "grant_fragments", "remove_hero"}:
        raise WalletError("invalid_payload")
    reason = _reason(reason)
    group, teacher, admin, scope = _operator_group(class_id=class_id, teacher=teacher, admin=admin)
    students = wallet_students(group).filter(pk=student_id)
    if not students.exists():
        raise Http404
    wallet = _locked_wallet(student_id)
    if not students.exists():
        raise Http404

    payload = {"student": student_id, "class": group.pk, "action": action, "reason": reason, "fields": fields}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    identity = {"actor_scope": scope, "action": "inventory_edit", "request_id": _request_id(request_id)}
    operation, created = GameOperation.objects.get_or_create(
        **identity, defaults={
            "player_id": wallet.player_id, "payload_hash": digest,
            "actor_teacher": teacher, "actor_admin": admin,
        },
    )
    if not created:
        if operation.payload_hash != digest:
            raise WalletError("request_conflict", 409)
        return {**operation.result, "replayed": True}

    if action == "set_balances":
        result = _set_balances(wallet, operation, group, student_id, reason, fields)
    elif action == "grant_fragments":
        result = _grant_fragments(wallet, operation, fields)
    else:
        result = _remove_hero(wallet, fields)
    operation.result = result
    operation.save(update_fields=["result"])
    if action != "set_balances":
        TowerAuditEvent.objects.create(
            event_type=action, actor_teacher=teacher, actor_admin=admin, student_id=student_id,
            class_group=group, metadata={"operation_id": operation.pk, "reason": reason, **result},
        )
    return {**result, "replayed": False}


def _set_balances(wallet, operation, group, student_id, reason, fields):
    currencies = WalletEntry.Currency.values
    if any(type(fields[currency]) is not int or not 0 <= fields[currency] <= MAX_BALANCE for currency in currencies):
        raise WalletError("invalid_amount")
    current = {currency: getattr(wallet, currency) for currency in currencies}
    if fields["expected"] != current:
        raise WalletError("inventory_changed", 409)
    balances = {currency: fields[currency] for currency in currencies}
    if balances == current:
        raise WalletError("inventory_no_changes")
    for currency in currencies:
        delta = balances[currency] - current[currency]
        if delta:
            _record_wallet_delta(
                wallet=wallet, operation=operation, currency=currency, delta=delta,
                reason=reason, source=WalletEntry.Source.CORRECTION, student_id=student_id, group=group,
                reference="inventory",
            )
    return balances


def _grant_fragments(wallet, operation, fields):
    hero = get_object_or_404(HeroDefinition, pk=fields["hero_id"], is_active=True)
    quantity = _positive_amount(fields["quantity"])
    current = PlayerHero.objects.filter(player_id=wallet.player_id, hero=hero).first()
    fragments = current.fragments if current else 0
    if (current and current.assembled_at) or fragments + quantity > hero.required_fragments:
        raise WalletError("inventory_fragment_limit", 409)
    return {**_grant(wallet.player, hero, quantity, operation=operation), "before": fragments}


def _remove_hero(wallet, fields):
    card = PlayerHero.objects.filter(player_id=wallet.player_id, pk=fields["card_id"]).first()
    if card is None or card.fragments != fields["fragments"]:
        raise WalletError("inventory_changed", 409)
    items = PlayerCosmetic.objects.filter(
        player_id=wallet.player_id, source="character_set", character_set__entitlement__hero_id=card.hero_id,
    )
    removed_items = list(items.order_by("cosmetic_id").values_list("cosmetic_id", flat=True))
    if removed_items:
        items.delete()
        player = wallet.player
        removed = set(removed_items)
        player.avatar_accessories = {slot: slug for slot, slug in player.avatar_accessories.items() if slug not in removed}
        player.appearance = {slot: slug for slot, slug in player.appearance.items() if slug not in removed}
        player.save(update_fields=["avatar_accessories", "appearance"])
    result = {
        "hero_id": card.hero_id, "before": card.fragments, "fragments": 0,
        "was_assembled": card.assembled_at is not None, "was_favorite": card.is_favorite,
        "removed_cosmetics": removed_items,
    }
    card.delete()
    return result
