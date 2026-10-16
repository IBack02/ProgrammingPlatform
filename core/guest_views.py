"""Public guest entry and exclusive, expiring browser reservations."""
import uuid
from datetime import timedelta

import qrcode
from qrcode.image.svg import SvgPathFillImage
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .models import ClassGroup, Student
from .security import auth_version


LEASE_DURATION = timedelta(minutes=30)


def release_guest(student_id, token):
    if student_id and token:
        Student.objects.filter(id=student_id, guest_lease_token=token).update(
            guest_lease_token=None, guest_lease_expires_at=None,
        )


def claim_guest(group, token):
    """Conditional writes work on both SQLite and PostgreSQL, including races."""
    now = timezone.now()
    students = Student.objects.filter(class_group=group, is_active=True)
    existing = students.filter(guest_lease_token=token, guest_lease_expires_at__gt=now).first()
    if existing:
        return existing
    free = Q(guest_lease_expires_at__isnull=True) | Q(guest_lease_expires_at__lte=now)
    for student_id in students.filter(free).order_by("id").values_list("id", flat=True):
        try:
            with transaction.atomic():
                claimed = students.filter(free, id=student_id).update(
                    guest_lease_token=token, guest_lease_expires_at=now + LEASE_DURATION,
                )
        except IntegrityError:
            # Two simultaneous clicks from the same browser use the same token.
            return students.filter(guest_lease_token=token, guest_lease_expires_at__gt=now).first()
        if claimed:
            return students.get(id=student_id)
    return None


class GuestLeaseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        student_id = request.session.get("student_id")
        token = request.session.get("guest_lease_token")
        if student_id:
            guest = Student.objects.filter(id=student_id, class_group__is_guest=True).exists()
            if guest:
                now = timezone.now()
                valid = Student.objects.filter(
                    id=student_id, is_active=True, class_group__owner__is_active=True,
                    guest_lease_token=token, guest_lease_expires_at__gt=now,
                ) if token else Student.objects.none()
                if not valid.exists():
                    for key in list(request.session.keys()):
                        if key.startswith("student_") or key == "guest_lease_token":
                            request.session.pop(key, None)
                else:
                    valid.filter(guest_lease_expires_at__lt=now + timedelta(minutes=25)).update(
                        guest_lease_expires_at=now + LEASE_DURATION,
                    )
        response = self.get_response(request)
        if token and (request.session.get("student_id") != student_id or request.session.get("guest_lease_token") != token):
            release_guest(student_id, token)
        return response


@never_cache
@require_http_methods(["GET", "POST"])
def guest_entry(request, token):
    group = get_object_or_404(ClassGroup, guest_access_token=token, is_guest=True, owner__is_active=True)
    # A single visitor nonce is established before submitting the entry form.
    visitor_token = request.session.get("guest_visitor_token")
    if not visitor_token or request.session.get("guest_visitor_class_id") != group.id:
        visitor_token = str(uuid.uuid4())
        request.session["guest_visitor_token"] = visitor_token
        request.session["guest_visitor_class_id"] = group.id
    error = False
    if request.method == "POST":
        student = claim_guest(group, visitor_token)
        if student:
            if request.session.get("student_id") != student.id:
                release_guest(request.session.get("student_id"), request.session.get("guest_lease_token"))
            request.session.flush()
            request.session.update({
                "guest_visitor_token": visitor_token,
                "guest_visitor_class_id": group.id,
                "guest_lease_token": visitor_token,
                "student_id": student.id,
                "student_name": student.full_name,
                "student_class_id": student.class_group_id,
                "student_auth_version": auth_version(student.pin_hash),
                "student_logged_in_at": timezone.now().isoformat(),
            })
            return redirect("/student/dashboard/")
        error = True
    return render(request, "core/guest_entry.html", {"group": group, "all_busy": error}, status=409 if error else 200)


@require_GET
def guest_qr(request, class_id):
    from .views import _get_logged_in_teacher
    teacher = _get_logged_in_teacher(request)
    if not teacher:
        return redirect("/teacher/login/")
    group = get_object_or_404(ClassGroup, id=class_id, owner=teacher, is_guest=True)
    url = request.build_absolute_uri(reverse("guest_entry", args=[group.guest_access_token]))
    image = qrcode.make(url, image_factory=SvgPathFillImage, box_size=8, border=4)
    response = HttpResponse(image.to_string(), content_type="image/svg+xml")
    response["Cache-Control"] = "private, no-store"
    if request.GET.get("download") == "1":
        response["Content-Disposition"] = f'attachment; filename="guest-class-{group.id}.svg"'
    return response
