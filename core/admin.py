from django.contrib import admin
from .models import (
    ClassGroup, Student,
    Session, SessionClass,
    SessionTask, TaskTestCase,
    StudentSession, StudentTaskProgress,
    Submission,
    ActivityEvent, ActivityAggregate,
)


@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("full_name", "class_group", "is_active", "created_at")
    list_filter = ("class_group", "is_active")
    search_fields = ("full_name",)

    # Удобно, чтобы вводить PIN при создании/редактировании вручную:
    readonly_fields = ("created_at",)

    def save_model(self, request, obj, form, change):
        """
        Если ты хочешь вводить pin_hash как обычный PIN в админке,
        лучше сделать отдельную форму. Сейчас оставляем как есть (через код/API).
        """
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
    list_display = ("progress", "total_copies", "total_pastes", "tab_switches", "focus_lost_count", "active_time_seconds", "updated_at")
    readonly_fields = ("updated_at",)
