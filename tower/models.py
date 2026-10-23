from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


MAX_BALANCE = 9_007_199_254_740_991  # Exact integer in both PostgreSQL and browser JSON.


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Journal records cannot be changed; append a correction.")

    def delete(self):
        raise ValidationError("Journal records cannot be deleted.")


class AppendOnlyModel(models.Model):
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Journal records cannot be changed; append a correction.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Journal records cannot be deleted.")


class TowerPlayer(models.Model):
    student = models.OneToOneField("core.Student", on_delete=models.PROTECT, related_name="tower_player")
    created_at = models.DateTimeField(auto_now_add=True)
    pack_misses = models.PositiveSmallIntegerField(default=0)
    avatar_shape = models.CharField(max_length=16, default="square")
    avatar_color = models.CharField(max_length=7, default="#3b82f6")
    avatar_accessories = models.JSONField(default=dict, blank=True)
    profile_theme = models.CharField(max_length=24, default="midnight")


class Wallet(models.Model):
    player = models.OneToOneField(TowerPlayer, on_delete=models.PROTECT, related_name="wallet")
    niscoin = models.PositiveBigIntegerField(default=0)
    universal_shards = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(niscoin__gte=0, niscoin__lte=MAX_BALANCE), name="tower_niscoin_range"),
            models.CheckConstraint(condition=models.Q(universal_shards__gte=0, universal_shards__lte=MAX_BALANCE), name="tower_shards_range"),
        ]


class GameOperation(models.Model):
    player = models.ForeignKey(TowerPlayer, on_delete=models.PROTECT, related_name="operations")
    actor_scope = models.CharField(max_length=64)
    action = models.CharField(max_length=40)
    request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    actor_teacher = models.ForeignKey("core.Teacher", on_delete=models.PROTECT, null=True, blank=True)
    actor_admin = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor_scope", "action", "request_id"], name="tower_operation_request_once"),
        ]


class WalletEntry(AppendOnlyModel):
    class Currency(models.TextChoices):
        NISCOIN = "niscoin", "Niscoin"
        SHARDS = "universal_shards", "Universal shards"

    class Source(models.TextChoices):
        MANUAL = "manual_award", "Teacher award"
        LEARNING = "learning_reward", "Learning reward"
        PURCHASE = "purchase", "Purchase"
        CORRECTION = "correction", "Administrator correction"
        EXCHANGE = "fragment_exchange", "Fragment exchange"
        OVERFLOW = "fragment_overflow", "Surplus fragments"

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="entries")
    operation = models.ForeignKey(GameOperation, on_delete=models.PROTECT, related_name="entries")
    currency = models.CharField(max_length=24, choices=Currency.choices)
    delta = models.BigIntegerField()
    balance_after = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=240)
    reference = models.CharField(max_length=120, blank=True)
    source = models.CharField(max_length=32, choices=Source.choices)
    class_group = models.ForeignKey("core.ClassGroup", on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["operation", "currency"], name="tower_entry_operation_currency"),
            models.CheckConstraint(condition=~models.Q(delta=0), name="tower_entry_nonzero"),
            models.CheckConstraint(condition=models.Q(balance_after__gte=0, balance_after__lte=MAX_BALANCE), name="tower_entry_balance_range"),
            models.CheckConstraint(condition=models.Q(currency__in=["niscoin", "universal_shards"]), name="tower_entry_currency_valid"),
        ]
        indexes = [models.Index(fields=["wallet", "-id"], name="tower_wallet_history_idx")]


class TowerAuditEvent(AppendOnlyModel):
    event_type = models.CharField(max_length=40)
    actor_teacher = models.ForeignKey("core.Teacher", on_delete=models.PROTECT, null=True, blank=True)
    actor_admin = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    student = models.ForeignKey("core.Student", on_delete=models.PROTECT, null=True, blank=True)
    class_group = models.ForeignKey("core.ClassGroup", on_delete=models.PROTECT, null=True, blank=True)
    entry = models.OneToOneField(WalletEntry, on_delete=models.PROTECT, null=True, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class RewardPolicy(models.Model):
    class_group = models.OneToOneField("core.ClassGroup", on_delete=models.CASCADE, related_name="reward_policy")
    race_first = models.PositiveIntegerField(default=0)
    race_second = models.PositiveIntegerField(default=0)
    race_third = models.PositiveIntegerField(default=0)
    task_solved = models.PositiveIntegerField(default=0)
    page_opened = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class RewardBatch(AppendOnlyModel):
    actor_scope = models.CharField(max_length=64)
    request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["actor_scope", "request_id"], name="tower_reward_batch_once")]


class HeroDefinition(models.Model):
    slug = models.SlugField(primary_key=True)
    name = models.CharField(max_length=80)
    rarity = models.CharField(max_length=16)
    roles = models.JSONField(default=dict)
    portrait = models.CharField(max_length=160)
    required_fragments = models.PositiveSmallIntegerField(default=10)
    shard_cost = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(required_fragments__gt=0, shard_cost__gt=0), name="tower_hero_positive_cost")]


class PlayerHero(models.Model):
    player = models.ForeignKey(TowerPlayer, on_delete=models.PROTECT, related_name="heroes")
    hero = models.ForeignKey(HeroDefinition, on_delete=models.PROTECT)
    fragments = models.PositiveSmallIntegerField(default=0)
    assembled_at = models.DateTimeField(null=True, blank=True)
    is_favorite = models.BooleanField(default=False)
    discovered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["player", "hero"], name="tower_player_hero_once")]


class PackVersion(AppendOnlyModel):
    slug = models.SlugField(unique=True)
    config = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class PackOffer(models.Model):
    slug = models.SlugField(primary_key=True)
    version = models.ForeignKey(PackVersion, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True)


class PackOpening(AppendOnlyModel):
    player = models.ForeignKey(TowerPlayer, on_delete=models.PROTECT, related_name="pack_openings")
    operation = models.OneToOneField(GameOperation, on_delete=models.PROTECT)
    version = models.ForeignKey(PackVersion, on_delete=models.PROTECT)
    cost = models.PositiveIntegerField()
    drops = models.JSONField()
    pity_before = models.PositiveSmallIntegerField()
    pity_after = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class CosmeticDefinition(models.Model):
    slug = models.SlugField(primary_key=True)
    slot = models.CharField(max_length=16)
    labels = models.JSONField(default=dict)
    price = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)


class PlayerCosmetic(models.Model):
    player = models.ForeignKey(TowerPlayer, on_delete=models.PROTECT, related_name="cosmetics")
    cosmetic = models.ForeignKey(CosmeticDefinition, on_delete=models.PROTECT)
    operation = models.OneToOneField(GameOperation, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["player", "cosmetic"], name="tower_player_cosmetic_once")]
