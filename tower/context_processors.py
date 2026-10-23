from core.views import _get_logged_in_student, _get_logged_in_teacher

from .permissions import eligible_student_classes, is_tower_admin, teacher_can_manage_tower, tower_is_enabled


def tower_access(request):
    flags = {"tower_can_enter": False, "tower_can_manage": False}
    if not tower_is_enabled():
        return flags
    if request.session.get("student_id") and not request.session.get("guest_lease_token"):
        flags["tower_can_enter"] = eligible_student_classes(_get_logged_in_student(request)).exists()
    if request.session.get("teacher_id"):
        flags["tower_can_manage"] = teacher_can_manage_tower(_get_logged_in_teacher(request))
    if not flags["tower_can_manage"]:
        flags["tower_can_manage"] = is_tower_admin(request)
    return flags
