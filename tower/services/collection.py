"""Pack prices, random draws and collection fulfilment stay on the server."""
import secrets

from django.db import transaction
from django.utils import timezone

from tower.models import (
    CosmeticDefinition, GameOperation, HeroDefinition, PackOffer, PackOpening,
    PlayerCosmetic, PlayerHero, TowerAuditEvent, WalletEntry, MAX_BALANCE,
)
from .wallet import WalletError, _locked_wallet, _perform, _request_id
from .character_sets import grant_character_set


SHAPES = ("square", "diamond", "triangle", "pentagon", "hexagon")
COLORS = ("#3b82f6", "#ef4444", "#f59e0b", "#10b981", "#8b5cf6", "#ec4899", "#06b6d4", "#64748b")
ACCESSORY_SLOTS = ("head", "glasses", "mustache")
SHARD_NISCOIN_RATE = 5


def _identifier(value):
    if not isinstance(value, str) or not value or len(value) > 50:
        raise WalletError("invalid_identifier")
    return value


def _start(student, action, request_id, payload):
    key = _request_id(request_id)
    wallet = _locked_wallet(student.pk)
    operation = GameOperation.objects.filter(actor_scope=f"student:{student.pk}", action=action, request_id=key).first()
    if operation:
        if operation.result.get("input") != payload:
            raise WalletError("request_conflict", 409)
        return wallet, key, {**operation.result, "replayed": True}
    return wallet, key, None


def _finish(result, payload, **extra):
    operation = GameOperation.objects.get(entries__id=result["entry_id"])
    operation.result = {**result, "input": payload, **extra}
    operation.save(update_fields=["result"])
    return operation.result


def _debit(student, key, amount, action, reason, reference, currency=WalletEntry.Currency.NISCOIN, source=None):
    return _perform(student_id=student.pk, currency=currency, delta=-amount, reason=reason,
        request_id=key, action=action, actor_scope=f"student:{student.pk}",
        source=source or (WalletEntry.Source.EXCHANGE if currency == WalletEntry.Currency.SHARDS else WalletEntry.Source.PURCHASE),
        reference=reference)


def _draw(config, guaranteed=False):
    # PostgreSQL JSONB does not preserve object key order.
    weights = [(rarity, config["weights"][rarity]) for rarity in ("common", "rare", "epic", "legendary")
               if not guaranteed or rarity != "common"]
    ticket = secrets.randbelow(sum(weight for _, weight in weights))
    for rarity, weight in weights:
        if ticket < weight:
            return secrets.choice(config["heroes"][rarity]), rarity
        ticket -= weight
    raise WalletError("catalog_unavailable", 409)


def _grant(player, hero, quantity, operation=None):
    row, _ = PlayerHero.objects.get_or_create(player=player, hero=hero)
    added = min(quantity, max(0, hero.required_fragments - row.fragments))
    row.fragments += added
    completed = row.fragments >= hero.required_fragments and row.assembled_at is None
    if completed:
        row.assembled_at = timezone.now()
    row.save(update_fields=["fragments", "assembled_at"])
    result = {"hero_id": hero.pk, "quantity": quantity, "added": added, "overflow": quantity - added,
              "fragments": row.fragments, "required": hero.required_fragments, "completed": completed}
    if row.assembled_at:
        result["character_set"] = grant_character_set(player=player, hero=hero, operation=operation)
    return result


def legendary_chance(config, position):
    """Basis points per pack, independent of rare-slot protection and its outcome."""
    cycle = config.get("legendary_cycle_bps")
    return cycle[position % len(cycle)] if cycle else None


def _legendary_slot(config, chance):
    if chance is not None and secrets.randbelow(10000) >= 10000 - chance:
        return secrets.randbelow(config["slots"])
    return None


@transaction.atomic
def open_pack(*, student, offer, version, request_id):
    payload = {"offer": _identifier(offer), "version": _identifier(version)}
    wallet, key, replay = _start(student, "pack_open", request_id, payload)
    if replay:
        return replay
    listing = PackOffer.objects.select_related("version").filter(pk=offer, is_active=True).first()
    if not listing:
        raise WalletError("catalog_unavailable", 409)
    if listing.version.slug != version:
        raise WalletError("catalog_changed", 409)
    config = listing.version.config
    price = config["price"]
    if type(price) is not int or price <= 0:
        raise WalletError("catalog_unavailable", 409)
    result = _debit(student, key, price, "pack_open", "Коллекционный пак NIS", f"pack:{version}")
    operation = GameOperation.objects.get(entries__id=result["entry_id"])
    player = wallet.player
    before = player.pack_misses
    guaranteed = before >= config["pity_after"]
    cycle_before = player.pack_cycle_position
    chance = legendary_chance(config, cycle_before)
    legendary_slot = _legendary_slot(config, chance)
    drops, rare_found = [], False
    for index in range(config["slots"]):
        if index == legendary_slot:
            slug, rarity = secrets.choice(config["heroes"]["legendary"]), "legendary"
        else:
            slug, rarity = _draw(config, guaranteed and index == 0)
        hero = HeroDefinition.objects.get(pk=slug)
        quantity = config.get("legendary_fragments", config["fragments_per_slot"]) if rarity == "legendary" else config["fragments_per_slot"]
        drop = _grant(player, hero, quantity, operation=operation)
        drop["rarity"] = rarity
        rate = config.get("overflow_rates", {}).get(rarity, config["overflow_rate"])
        drop["shards"] = drop["overflow"] * rate
        drops.append(drop)
        rare_found = rare_found or rarity != "common"
    player.pack_misses = 0 if rare_found else before + 1
    if chance is not None:
        player.pack_cycle_position = (cycle_before + 1) % len(config["legendary_cycle_bps"])
    player.save(update_fields=["pack_misses", "pack_cycle_position"])
    surplus = sum(drop["shards"] for drop in drops)
    if surplus:
        _perform(student_id=student.pk, currency=WalletEntry.Currency.SHARDS, delta=surplus,
            reason="Избыток фрагментов из пака", request_id=key, action="pack_overflow",
            actor_scope=f"student:{student.pk}", source=WalletEntry.Source.OVERFLOW, reference=f"pack:{version}")
    opening = PackOpening.objects.create(player=player, operation_id=GameOperation.objects.get(entries__id=result["entry_id"]).pk,
        version=listing.version, cost=price, drops=drops, pity_before=before, pity_after=player.pack_misses)
    return _finish(result, payload, opening_id=opening.pk, drops=drops, shards=surplus, pity=player.pack_misses,
        guaranteed=guaranteed, legendary_chance_bps=chance, cycle_before=cycle_before,
        cycle_after=player.pack_cycle_position)


def exchange_fragments(*, student, hero_id, quantity, request_id):
    """Reject legacy callers without changing balances or historical receipts."""
    raise WalletError("fragment_exchange_disabled", 403)


@transaction.atomic
def convert_shards(*, student, quantity, request_id):
    if type(quantity) is not int or not 1 <= quantity <= MAX_BALANCE // SHARD_NISCOIN_RATE:
        raise WalletError("invalid_amount")
    payload = {"quantity": quantity}
    wallet, key, replay = _start(student, "shards_to_coins", request_id, payload)
    if replay:
        return replay
    coins = quantity * SHARD_NISCOIN_RATE
    before = wallet.niscoin
    if before + coins > MAX_BALANCE:
        raise WalletError("balance_limit", 409)
    result = _debit(student, key, quantity, "shards_to_coins", "Обмен осколков на нишкоины",
                    "shards-to-coins:5", WalletEntry.Currency.SHARDS, source=WalletEntry.Source.CONVERSION)
    operation = GameOperation.objects.get(entries__id=result["entry_id"])
    wallet.niscoin = before + coins
    wallet.updated_at = timezone.now()
    wallet.save(update_fields=["niscoin", "updated_at"])
    credit = WalletEntry.objects.create(wallet=wallet, operation=operation,
        currency=WalletEntry.Currency.NISCOIN, delta=coins, balance_after=wallet.niscoin,
        reason="Нишкоины за обмен осколков", source=WalletEntry.Source.CONVERSION, reference="shards-to-coins:5")
    TowerAuditEvent.objects.create(event_type=WalletEntry.Source.CONVERSION, student_id=student.pk, entry=credit,
        metadata={"currency": WalletEntry.Currency.NISCOIN, "before": before, "after": wallet.niscoin,
                  "delta": coins, "shards": quantity, "rate": SHARD_NISCOIN_RATE})
    return _finish(result, payload, shards_spent=quantity, coins_received=coins, rate=SHARD_NISCOIN_RATE,
                   credit_entry_id=credit.pk, niscoin=wallet.niscoin, universal_shards=result["balance_after"])


@transaction.atomic
def buy_cosmetic(*, student, cosmetic_id, request_id):
    payload = {"cosmetic_id": _identifier(cosmetic_id)}
    wallet, key, replay = _start(student, "cosmetic_purchase", request_id, payload)
    if replay:
        return replay
    item = CosmeticDefinition.objects.filter(pk=cosmetic_id, is_active=True, acquisition="purchase").first()
    if not item:
        raise WalletError("catalog_unavailable", 409)
    if PlayerCosmetic.objects.filter(player=wallet.player, cosmetic=item).exists():
        raise WalletError("already_owned", 409)
    result = _debit(student, key, item.price, "cosmetic_purchase", f"Оформление: {item.labels.get('ru', item.pk)}", f"cosmetic:{item.pk}")
    PlayerCosmetic.objects.create(player=wallet.player, cosmetic=item, operation_id=GameOperation.objects.get(entries__id=result["entry_id"]).pk)
    return _finish(result, payload, cosmetic_id=item.pk)


@transaction.atomic
def save_avatar(*, student, shape, color, accessories, theme):
    wallet = _locked_wallet(student.pk)
    if shape not in SHAPES or color not in COLORS or not isinstance(accessories, dict) or set(accessories) != set(ACCESSORY_SLOTS):
        raise WalletError("invalid_avatar")
    selected = {key: value for key, value in accessories.items() if value}
    if any(not isinstance(value, str) for value in selected.values()) or not isinstance(theme, str):
        raise WalletError("invalid_avatar")
    if theme != "midnight":
        selected["theme"] = theme
    owned = {row.cosmetic_id: row.cosmetic for row in PlayerCosmetic.objects.filter(
        player=wallet.player, cosmetic__acquisition="purchase").select_related("cosmetic")}
    if any(slug not in owned or owned[slug].slot != slot for slot, slug in selected.items()):
        raise WalletError("item_not_owned", 403)
    player = wallet.player
    player.avatar_shape, player.avatar_color = shape, color
    player.avatar_accessories = {slot: slug for slot, slug in selected.items() if slot != "theme"}
    player.profile_theme = theme
    player.save(update_fields=["avatar_shape", "avatar_color", "avatar_accessories", "profile_theme"])
    return player


@transaction.atomic
def set_favorite(*, student, hero_id, favorite):
    wallet = _locked_wallet(student.pk)
    if type(favorite) is not bool:
        raise WalletError("invalid_payload")
    row = PlayerHero.objects.filter(player=wallet.player, hero_id=hero_id, assembled_at__isnull=False).first()
    if not row:
        raise WalletError("not_collected", 409)
    if favorite and not row.is_favorite and PlayerHero.objects.filter(player=wallet.player, is_favorite=True).count() >= 3:
        raise WalletError("favorite_limit", 409)
    row.is_favorite = favorite
    row.save(update_fields=["is_favorite"])
