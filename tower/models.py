import uuid

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
    appearance = models.JSONField(default=dict, blank=True)


class CollectionCredential(models.Model):
    """Separate from the student's lesson PIN; never stores a raw password."""
    student = models.OneToOneField("core.Student", on_delete=models.CASCADE, related_name="collection_credential")
    password_hash = models.CharField(max_length=256, editable=False)
    updated_at = models.DateTimeField(auto_now=True)


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
    class Acquisition(models.TextChoices):
        PURCHASE = "purchase", "Purchase"
        CHARACTER_SET = "character_set", "Character set"

    slug = models.SlugField(primary_key=True)
    slot = models.CharField(max_length=16)
    labels = models.JSONField(default=dict)
    price = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    acquisition = models.CharField(max_length=16, choices=Acquisition.choices, default=Acquisition.PURCHASE)
    set_version = models.ForeignKey("CharacterSetVersion", null=True, blank=True, on_delete=models.PROTECT)
    appearance = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(acquisition="purchase", set_version__isnull=True, price__gt=0)
            | models.Q(acquisition="character_set", set_version__isnull=False, price=0)
        ), name="tower_cosmetic_acquisition")]


class PlayerCosmetic(models.Model):
    player = models.ForeignKey(TowerPlayer, on_delete=models.PROTECT, related_name="cosmetics")
    cosmetic = models.ForeignKey(CosmeticDefinition, on_delete=models.PROTECT)
    operation = models.ForeignKey(GameOperation, on_delete=models.PROTECT, null=True, blank=True, related_name="cosmetic_grants")
    source = models.CharField(max_length=16, choices=CosmeticDefinition.Acquisition.choices, default="purchase")
    character_set = models.ForeignKey("PlayerCharacterSet", null=True, blank=True, on_delete=models.PROTECT, related_name="items")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["player", "cosmetic"], name="tower_player_cosmetic_once"),
            models.CheckConstraint(condition=(
                models.Q(source="purchase", operation__isnull=False, character_set__isnull=True)
                | models.Q(source="character_set", operation__isnull=True, character_set__isnull=False)
            ), name="tower_cosmetic_grant_source"),
        ]


class CharacterSetVersion(AppendOnlyModel):
    slug = models.SlugField(max_length=100, unique=True)
    hero = models.ForeignKey(HeroDefinition, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField()
    config = models.JSONField()
    behavior = models.JSONField()
    asset_manifest = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hero", "revision"], name="tower_set_hero_revision"),
            models.CheckConstraint(condition=models.Q(revision__gt=0), name="tower_set_revision_positive"),
        ]


class CharacterSetPublication(models.Model):
    hero = models.OneToOneField(HeroDefinition, primary_key=True, on_delete=models.PROTECT)
    version = models.ForeignKey(CharacterSetVersion, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True)


class CharacterSetEntitlement(AppendOnlyModel):
    player = models.ForeignKey(TowerPlayer, on_delete=models.PROTECT, related_name="set_entitlements")
    hero = models.ForeignKey(HeroDefinition, on_delete=models.PROTECT)
    operation = models.ForeignKey(GameOperation, on_delete=models.PROTECT, null=True, blank=True)
    earned_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["player", "hero"], name="tower_set_entitlement_once")]


class PlayerCharacterSet(AppendOnlyModel):
    entitlement = models.OneToOneField(CharacterSetEntitlement, on_delete=models.PROTECT, related_name="fulfilment")
    version = models.ForeignKey(CharacterSetVersion, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class CompanionLearningVisit(models.Model):
    student = models.ForeignKey("core.Student", on_delete=models.CASCADE, related_name="companion_visits")
    session = models.ForeignKey("core.Session", on_delete=models.CASCADE)
    module_type = models.CharField(max_length=24)
    resource_id = models.PositiveBigIntegerField()
    first_visited_at = models.DateTimeField()
    last_visited_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["student", "module_type", "resource_id"], name="tower_learning_visit_once")]
        indexes = [models.Index(fields=["student", "-last_visited_at"], name="tower_visit_recent_idx")]


class CompanionMessage(models.Model):
    """A source reservation, then saved reply, then acknowledged delivery."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey("core.Student", on_delete=models.CASCADE, related_name="companion_messages")
    source_kind = models.CharField(max_length=24)
    resource_type = models.CharField(max_length=24)
    resource_id = models.PositiveBigIntegerField()
    revision = models.CharField(max_length=64)
    source_snapshot = models.JSONField()
    text = models.TextField(blank=True, default="")
    reserved_until = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    generation_status = models.CharField(max_length=16, blank=True, default="")
    hero_id = models.CharField(max_length=80, blank=True, default="")
    language = models.CharField(max_length=2, blank=True, default="")
    behavior_snapshot = models.JSONField(default=dict, blank=True)
    failure_code = models.CharField(max_length=40, blank=True, default="")
    model = models.CharField(max_length=80, blank=True, default="")
    tokens_in = models.PositiveIntegerField(null=True, blank=True)
    tokens_out = models.PositiveIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    provider_request_id = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["student", "-created_at"], name="tower_message_recent_idx"),
                   models.Index(fields=["student", "source_kind", "resource_type", "resource_id"], name="tower_source_history_idx")]
        constraints = [
            models.CheckConstraint(condition=(models.Q(completed_at__isnull=True, text="", delivered_at__isnull=True)
                | (models.Q(completed_at__isnull=False) & ~models.Q(text=""))), name="tower_message_completion"),
        ]


class CompanionAccountState(models.Model):
    student = models.OneToOneField("core.Student", primary_key=True, on_delete=models.CASCADE)
    active_message = models.ForeignKey(CompanionMessage, null=True, blank=True, on_delete=models.SET_NULL)
    last_success_at = models.DateTimeField(null=True, blank=True)
    retry_after = models.DateTimeField(null=True, blank=True)


class CompanionRequest(models.Model):
    student = models.ForeignKey("core.Student", on_delete=models.CASCADE)
    request_id = models.UUIDField()
    message = models.ForeignKey(CompanionMessage, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["student", "request_id"], name="tower_companion_request_once")]
