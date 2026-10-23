import uuid

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from .forms import AdjustmentForm
from .models import GameOperation, TowerAuditEvent, Wallet, WalletEntry
from .services.wallet import WalletError, adjust_wallet


class JournalAdmin(admin.ModelAdmin):
    actions = None

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(Wallet)
class WalletAdmin(JournalAdmin):
    list_display = ("id", "student_name", "niscoin", "universal_shards", "updated_at")
    search_fields = ("player__student__full_name",)
    list_select_related = ("player__student",)
    change_form_template = "admin/tower/wallet/change_form.html"

    @admin.display(description="Ученик")
    def student_name(self, obj):
        return obj.player.student.full_name

    def get_urls(self):
        return [path("<int:wallet_id>/adjust/", self.admin_site.admin_view(self.adjust), name="tower_wallet_adjust")] + super().get_urls()

    def adjust(self, request, wallet_id):
        if not self.has_view_permission(request):
            raise PermissionDenied
        wallet = get_object_or_404(Wallet.objects.select_related("player__student"), pk=wallet_id)
        form = AdjustmentForm(request.POST if request.method == "POST" else None, initial={"request_id": uuid.uuid4(), "currency": "niscoin"})
        if request.method == "POST" and form.is_valid():
            try:
                result = adjust_wallet(admin=request.user, student_id=wallet.player.student_id, **form.cleaned_data)
            except WalletError as exc:
                errors = {
                    "insufficient_funds": "Недостаточно средств: баланс не может быть отрицательным.",
                    "request_conflict": "Этот запрос уже использован. Обновите страницу.",
                    "balance_limit": "Достигнут предельный баланс.",
                }
                form.add_error(None, errors.get(exc.code, "Проверьте сумму, валюту и причину."))
            else:
                self.message_user(request, "Корректировка уже учтена." if result["replayed"] else "Корректировка записана в журнал.", messages.SUCCESS)
                return redirect(reverse("admin:tower_wallet_change", args=[wallet.pk]))
        context = {
            **self.admin_site.each_context(request), "title": "Корректировка кошелька", "opts": self.model._meta,
            "original": wallet, "wallet": wallet, "form": form,
        }
        return TemplateResponse(request, "admin/tower/wallet/adjust.html", context)


@admin.register(WalletEntry)
class WalletEntryAdmin(JournalAdmin):
    list_display = ("id", "wallet", "currency", "delta", "balance_after", "source", "created_at")
    list_filter = ("currency", "source")
    search_fields = ("wallet__player__student__full_name", "reason")
    list_select_related = ("wallet",)


@admin.register(GameOperation)
class GameOperationAdmin(JournalAdmin):
    list_display = ("id", "actor_scope", "action", "request_id", "created_at")
    search_fields = ("request_id", "actor_scope")


@admin.register(TowerAuditEvent)
class TowerAuditEventAdmin(JournalAdmin):
    list_display = ("id", "event_type", "actor_teacher", "actor_admin", "student", "class_group", "created_at")
    list_filter = ("event_type",)
    list_select_related = ("actor_teacher", "actor_admin", "student", "class_group")
