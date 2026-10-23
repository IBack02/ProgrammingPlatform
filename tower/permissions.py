"""Tower authorization uses core's student/teacher sessions, not Django User roles."""

from functools import wraps

from django.conf import settings
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from core.models import ClassGroup
from core.ui_translations import UI_TRANSLATIONS, get_ui_lang
from core.views import _get_logged_in_student, _get_logged_in_teacher


def tower_is_enabled():
    return bool(settings.TOWER_ENABLED)


def is_tower_admin(request):
    user = request.user
    return user.is_authenticated and user.is_active and user.is_superuser


def teacher_can_manage_tower(teacher):
    return bool(tower_is_enabled() and teacher and teacher.is_active and teacher.can_manage_tower)


def eligible_student_classes(student):
    if (
        not tower_is_enabled()
        or not student
        or not student.is_active
        or student.class_group.is_guest
        or student.guest_lease_token is not None
    ):
        return ClassGroup.objects.none()
    return ClassGroup.objects.filter(
        Q(pk=student.class_group_id) | Q(student_memberships__student_id=student.pk),
        tower_enabled=True,
        is_guest=False,
        owner__is_active=True,
        owner__can_manage_tower=True,
    ).distinct().order_by("name", "id")


def managed_classes(request):
    groups = ClassGroup.objects.select_related("owner").order_by("name", "id")
    if is_tower_admin(request):
        return groups
    return groups.filter(owner=request.tower_teacher)


def _denied(request, *, authenticated, login_url, role):
    if request.path.startswith("/api/"):
        code = "tower_access_denied" if authenticated else "not_authenticated"
        if authenticated and not tower_is_enabled():
            code = "tower_disabled"
        return JsonResponse({"ok": False, "error": code}, status=403 if authenticated else 401)
    if not authenticated:
        return redirect(login_url)
    text = UI_TRANSLATIONS[get_ui_lang(request)]
    return_url = "/teacher/" if role == "teacher" else "/student/"
    context = {"tower_return_url": "/admin/" if is_tower_admin(request) else return_url}
    if not tower_is_enabled():
        context["tower_denial_hint"] = text["tower_disabled_hint"]
        if is_tower_admin(request):
            context["tower_admin_setup_hint"] = text["tower_disabled_admin_hint"]
    elif role == "teacher":
        if request.session.get("teacher_id"):
            context["tower_denial_hint"] = text["tower_teacher_permission_hint"]
        else:
            context.update({
                "tower_denial_hint": text["tower_teacher_login_hint"],
                "tower_login_url": login_url,
            })
    return render(request, "tower/denied.html", context, status=403)


def tower_student_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        student = _get_logged_in_student(request)
        if student and not request.session.get("guest_lease_token"):
            groups = list(eligible_student_classes(student))
            if groups:
                request.tower_student = student
                request.tower_classes = groups
                return view(request, *args, **kwargs)
        authenticated = bool(student or _get_logged_in_teacher(request) or request.user.is_authenticated)
        return _denied(request, authenticated=authenticated, login_url="/student/login/", role="student")

    return never_cache(wrapped)


def tower_teacher_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        teacher = _get_logged_in_teacher(request)
        if tower_is_enabled() and (is_tower_admin(request) or teacher_can_manage_tower(teacher)):
            request.tower_teacher = teacher
            return view(request, *args, **kwargs)
        authenticated = bool(teacher or _get_logged_in_student(request) or request.user.is_authenticated)
        return _denied(request, authenticated=authenticated, login_url="/teacher/login/", role="teacher")

    return never_cache(wrapped)
