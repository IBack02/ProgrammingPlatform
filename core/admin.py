from django.contrib import admin
from django.conf import settings
from django.db import transaction


def _save_tower_access(model_admin, request, obj, form, change, fields, event_type):
    from tower.models import TowerAuditEvent

    with transaction.atomic():
        previous = type(obj).objects.filter(pk=obj.pk).values(*fields).first() if change else None
        model_admin.save_model(request, obj, form, change)
        current = {field: getattr(obj, field) for field in fields}
        if previous != current and (change or any(current.get(key) for key in ("can_manage_tower", "tower_enabled"))):
            TowerAuditEvent.objects.create(
                event_type=event_type, actor_admin=request.user,
                class_group=obj if event_type == "class_admin_access" else None,
                metadata={"target_id": obj.pk, "before": previous, "after": current},
            )

from .forms import ClassGroupAdminForm, StudentAdminForm
from .models import (
    ClassGroup,
    Student,
    StudentClassMembership,
    Session,
    SessionClass,
    SessionTask,
    TaskTestCase,
    StudentSession,
    StudentTaskProgress,
    Submission,
    ActivityEvent,
    ActivityAggregate,
    AiAssistMessage,
    TaskCodeFragment,  # NEW,
    Exam, ExamClass, ExamQuestion, ExamMatchPair, ExamAttempt, ExamAnswer,
    PeerAssessmentSession, PeerAssessmentAssignment, PeerAssessmentReview,
    GameModule, MindRacePrompt, GameRound, GameParticipant,
)


class StudentClassMembershipInline(admin.TabularInline):
    model = StudentClassMembership
    extra = 0


@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    form = ClassGroupAdminForm
    list_display = ("id", "name", "owner", "tower_enabled", "created_at")
    list_filter = ("tower_enabled",)
    search_fields = ("name",)

    def save_model(self, request, obj, form, change):
        _save_tower_access(super(), request, obj, form, change,
                           ("tower_enabled", "owner_id", "is_guest"), "class_admin_access")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if request.user.is_superuser else (*fields, "tower_enabled")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    form = StudentAdminForm
    list_display = ("full_name", "class_group", "is_active", "created_at")
    list_filter = ("class_group", "is_active")
    search_fields = ("full_name",)
    readonly_fields = ("created_at",)
    exclude = ("pin_hash",)
    inlines = (StudentClassMembershipInline,)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)


class SessionClassInline(admin.TabularInline):
    model = SessionClass
    extra = 1


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "starts_at", "ends_at", "created_at")
    list_filter = ("status",)
    search_fields = ("title",)
    inlines = (SessionClassInline,)


class TaskTestCaseInline(admin.TabularInline):
    model = TaskTestCase
    extra = 1


@admin.register(SessionTask)
class SessionTaskAdmin(admin.ModelAdmin):
    list_display = ("session", "position", "title", "created_at")
    list_filter = ("session",)
    search_fields = ("title",)
    inlines = (TaskTestCaseInline,)


@admin.register(TaskCodeFragment)
class TaskCodeFragmentAdmin(admin.ModelAdmin):
    list_display = ("task", "position", "title", "is_active", "created_at")
    list_filter = ("position", "is_active", "task__session")
    search_fields = ("task__title", "title", "code")
    readonly_fields = ("created_at",)


@admin.register(AiAssistMessage)
class AiAssistMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "progress", "level", "status", "model", "created_at")
    list_filter = ("level", "status", "model", "created_at")
    search_fields = ("progress__student_session__student__full_name", "error_message")
    readonly_fields = ("created_at",)


@admin.register(StudentSession)
class StudentSessionAdmin(admin.ModelAdmin):
    list_display = ("student", "session", "started_at", "finished_at", "finish_reason")
    list_filter = ("session", "finish_reason")
    search_fields = ("student__full_name", "session__title")


@admin.register(StudentTaskProgress)
class StudentTaskProgressAdmin(admin.ModelAdmin):
    list_display = ("student_session", "task", "status", "attempts_total", "attempts_failed", "opened_at", "solved_at")
    list_filter = ("status",)
    search_fields = ("student_session__student__full_name", "task__title")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "progress", "attempt_no", "verdict", "passed_tests", "total_tests", "submitted_at")
    list_filter = ("verdict",)
    search_fields = ("progress__student_session__student__full_name",)
    readonly_fields = ("submitted_at",)


@admin.register(ActivityEvent)
class ActivityEventAdmin(admin.ModelAdmin):
    list_display = ("progress", "event_type", "occurred_at")
    list_filter = ("event_type",)
    search_fields = ("progress__student_session__student__full_name",)
    readonly_fields = ("occurred_at",)


@admin.register(ActivityAggregate)
class ActivityAggregateAdmin(admin.ModelAdmin):
    list_display = (
        "progress",
        "total_copies",
        "total_pastes",
        "tab_switches",
        "focus_lost_count",
        "active_time_seconds",
        "hint1_requests",
        "hint2_requests",
        "updated_at",
    )
    readonly_fields = ("updated_at",)
from .models import Teacher
from .forms import TeacherAdminForm

@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    form = TeacherAdminForm
    list_display = ("full_name", "is_active", "can_manage_tower", "created_at")
    list_filter = ("is_active", "can_manage_tower")
    search_fields = ("full_name",)
    readonly_fields = ("created_at",)
    exclude = ("pin_hash",)
    change_form_template = "admin/core/teacher/change_form.html"
    change_list_template = "admin/core/teacher/change_list.html"

    def render_change_form(self, request, context, *args, **kwargs):
        context["tower_service_enabled"] = bool(settings.TOWER_ENABLED)
        return super().render_change_form(request, context, *args, **kwargs)

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, extra_context={
            **(extra_context or {}), "tower_service_enabled": bool(settings.TOWER_ENABLED),
        })

    def save_model(self, request, obj, form, change):
        _save_tower_access(super(), request, obj, form, change,
                           ("can_manage_tower", "is_active"), "teacher_access")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if request.user.is_superuser else (*fields, "can_manage_tower")

admin.site.register([Exam, ExamClass, ExamQuestion, ExamMatchPair, ExamAttempt, ExamAnswer])
admin.site.register([PeerAssessmentSession, PeerAssessmentAssignment, PeerAssessmentReview])
admin.site.register([GameModule, MindRacePrompt, GameRound, GameParticipant])


# ---------------------------------------------------------------------------
# 502 / 5xx diagnostics
# ---------------------------------------------------------------------------
import json as _json
from datetime import timedelta as _timedelta

from django.utils import timezone as _tz
from django.utils.html import format_html as _format_html, format_html_join as _format_html_join

from .models import ServerErrorReport

_PRE = '<pre style="white-space:pre-wrap;max-height:520px;overflow:auto;margin:0">{}</pre>'


def _pretty_json(value):
    return _format_html(_PRE, _json.dumps(value or {}, ensure_ascii=False, indent=2, default=str))


@admin.register(ServerErrorReport)
class ServerErrorReportAdmin(admin.ModelAdmin):
    list_display = (
        "last_seen_at", "status_badge", "source", "method", "short_path",
        "occurrences", "duration_ms", "user_label", "exception_type", "is_resolved",
    )
    list_display_links = ("last_seen_at", "short_path")
    list_filter = ("is_resolved", "source", "status_code", "user_role", "last_seen_at")
    search_fields = ("path", "route", "request_id", "edge_ray_id", "exception_type", "exception_message", "user_label")
    date_hierarchy = "last_seen_at"
    actions = ("mark_resolved", "mark_unresolved")
    list_per_page = 50
    readonly_fields = (
        "source", "status_code", "method", "path", "route", "request_id", "edge_ray_id",
        "user_role", "user_id", "user_label", "user_agent", "duration_ms", "occurrences",
        "first_seen_at", "last_seen_at", "exception_type", "exception_message",
        "traceback_display", "where_it_hung", "server_context_display", "client_context_display",
        "related_reports", "resolved_at", "fingerprint",
    )
    fieldsets = (
        ("Что случилось", {"fields": (
            ("source", "status_code", "occurrences"), ("method", "path"), "route",
            ("first_seen_at", "last_seen_at", "duration_ms"),
        )}),
        ("Кто и где", {"fields": (("user_role", "user_id", "user_label"), ("request_id", "edge_ray_id"), "user_agent")}),
        ("Ошибка", {"fields": ("exception_type", "exception_message", "traceback_display", "where_it_hung")}),
        ("Собранные логи", {"fields": ("server_context_display", "client_context_display", "related_reports")}),
        ("Разбор", {"fields": ("is_resolved", "resolved_at", "admin_note", "fingerprint")}),
    )

    def has_add_permission(self, request):
        return False

    @admin.display(description="Код", ordering="status_code")
    def status_badge(self, obj):
        color = "#b91c1c" if obj.status_code in (502, 503, 504) else "#b45309" if obj.status_code >= 500 else "#475569"
        return _format_html('<b style="color:{}">{}</b>', color, obj.status_code)

    @admin.display(description="Путь", ordering="path")
    def short_path(self, obj):
        return obj.path if len(obj.path) <= 70 else obj.path[:69] + "…"

    @admin.display(description="Traceback")
    def traceback_display(self, obj):
        return _format_html(_PRE, obj.traceback) if obj.traceback else "—"

    @admin.display(description="Где завис воркер (стек потока запроса)")
    def where_it_hung(self, obj):
        threads = (obj.server_context or {}).get("threads") or []
        if not threads:
            return "—"
        # The request thread is the one whose stack goes through Django's handler.
        busy = [row for row in threads if "django/core/handlers" in row.get("stack", "").replace("\\", "/")]
        text = "\n\n".join(
            f"# {row.get('name')} ({row.get('thread_id')})\n{row.get('stack', '')}" for row in (busy or threads)
        )
        return _format_html(_PRE, text)

    @admin.display(description="Контекст сервера")
    def server_context_display(self, obj):
        context = dict(obj.server_context or {})
        context.pop("threads", None)
        return _pretty_json(context)

    @admin.display(description="Контекст браузера")
    def client_context_display(self, obj):
        return _pretty_json(obj.client_context)

    @admin.display(description="Связанные отчёты")
    def related_reports(self, obj):
        rows = list((obj.server_context or {}).get("related_reports") or [])
        if obj.request_id:
            rows += [
                {"id": row.id, "source": row.source, "status": row.status_code}
                for row in ServerErrorReport.objects.filter(request_id=obj.request_id).exclude(pk=obj.pk)[:10]
            ]
        unique = list({row["id"]: row for row in rows if row.get("id") != obj.pk}.values())
        if not unique:
            return "—"
        return _format_html_join(
            _format_html("<br>"), '<a href="../../{}/change/">#{} · {} · {}</a>',
            ((row["id"], row["id"], row.get("status"), row.get("source")) for row in unique),
        )

    def save_model(self, request, obj, form, change):
        if "is_resolved" in form.changed_data:
            obj.resolved_at = _tz.now() if obj.is_resolved else None
        super().save_model(request, obj, form, change)

    @admin.action(description="Отметить как исправленные")
    def mark_resolved(self, request, queryset):
        queryset.update(is_resolved=True, resolved_at=_tz.now())

    @admin.action(description="Вернуть в работу")
    def mark_unresolved(self, request, queryset):
        queryset.update(is_resolved=False, resolved_at=None)

    def changelist_view(self, request, extra_context=None):
        since = _tz.now() - _timedelta(hours=24)
        open_reports = ServerErrorReport.objects.filter(is_resolved=False)
        recent = open_reports.filter(last_seen_at__gte=since)
        extra_context = {
            **(extra_context or {}),
            "subtitle": (
                f"Открыто: {open_reports.count()} · за 24 ч: {recent.count()}"
                f" · из них 502/503/504: {recent.filter(status_code__in=[502, 503, 504]).count()}"
            ),
        }
        return super().changelist_view(request, extra_context=extra_context)
