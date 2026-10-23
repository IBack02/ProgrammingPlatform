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
        PURCHASE = "purchase", "Purchase"
        CORRECTION = "correction", "Administrator correction"

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
