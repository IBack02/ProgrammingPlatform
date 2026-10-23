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
