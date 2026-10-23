from django.db.models import Q

from core.models import Student
from tower.models import TowerPlayer
from tower.permissions import eligible_student_classes, tower_is_enabled


def saved_avatar(student):
    if not eligible_student_classes(student).exists():
        return None
    player = TowerPlayer.objects.filter(student=student).first()
    if not player:
        return None
    return {"avatar_shape": player.avatar_shape, "avatar_color": player.avatar_color,
            "avatar_accessories": player.avatar_accessories}


def prepare_avatar_rows(participants):
    """One access query for an entire roster; never expose revoked decorations."""
    rows = [row for row in participants if row is not None and not hasattr(row, "_tower_accessories")]
    decorated_ids = [row.student_id for row in rows if row.avatar_accessories]
    allowed = set()
    if decorated_ids and tower_is_enabled():
        allowed = set(Student.objects.filter(pk__in=decorated_ids, is_active=True,
            class_group__is_guest=False, guest_lease_token__isnull=True).filter(
                Q(class_group__tower_enabled=True, class_group__owner__is_active=True, class_group__owner__can_manage_tower=True)
                | Q(class_memberships__class_group__tower_enabled=True, class_memberships__class_group__is_guest=False,
                    class_memberships__class_group__owner__is_active=True, class_memberships__class_group__owner__can_manage_tower=True)
            ).values_list("pk", flat=True).distinct())
    for row in rows:
        row._tower_accessories = row.avatar_accessories if row.student_id in allowed else {}
