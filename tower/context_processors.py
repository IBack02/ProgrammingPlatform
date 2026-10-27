from core.views import _get_logged_in_student, _get_logged_in_teacher

from .permissions import eligible_student_classes, is_tower_admin, teacher_can_manage_tower, tower_is_enabled


def tower_access(request):
    flags = {"tower_can_enter": False, "tower_can_manage": False}
    if not tower_is_enabled():
        return flags
    if request.session.get("student_id") and not request.session.get("guest_lease_token"):
        student = getattr(request, "tower_student", None) or _get_logged_in_student(request)
        flags["tower_can_enter"] = eligible_student_classes(student).exists()
        if flags["tower_can_enter"] and not request.path.startswith("/api/"):
            from core.ui_translations import get_ui_lang
            from .models import TowerPlayer
            from .services.appearance import appearance_context
            player = TowerPlayer.objects.filter(student=student).first()
            if player:
                flags["tower_appearance"] = appearance_context(player, get_ui_lang(request))
                flags["tower_appearance_player"] = player.pk
                from .services.companion import companion_enabled
                flags["tower_companion_enabled"] = companion_enabled(student)
    if request.session.get("teacher_id"):
        flags["tower_can_manage"] = teacher_can_manage_tower(_get_logged_in_teacher(request))
    if not flags["tower_can_manage"]:
        flags["tower_can_manage"] = is_tower_admin(request)
    return flags
