from tower.models import TowerAuditEvent


def record_access_change(request, event_type, *, group=None, student=None, before, after):
    operator = request.user if request.user.is_authenticated else None
    return TowerAuditEvent.objects.create(
        event_type=event_type, class_group=group, student=student,
        actor_admin=operator,
        actor_teacher=None if operator else getattr(request, "tower_teacher", None),
        metadata={"before": before, "after": after},
    )
