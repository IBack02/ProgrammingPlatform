"""A second authentication boundary for the journal and its spending APIs."""
import hmac
from functools import wraps
from urllib.parse import urlencode

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.http import JsonResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from core.models import Student
from core.security import auth_version
from .models import CollectionCredential

SESSION_KEY = "tower_collection_access"
IDLE_SECONDS = 15 * 60


def lock_collection(request):
    request.session.pop(SESSION_KEY, None)


def collection_unlocked(request, *, touch=False):
    grant = request.session.get(SESSION_KEY)
    student_id = request.session.get("student_id")
    if not isinstance(grant, dict) or grant.get("student_id") != student_id:
        return False
    credential = CollectionCredential.objects.filter(student_id=student_id).first()
    now = timezone.now().timestamp()
    last_seen = grant.get("last_seen")
    if (not credential or not isinstance(last_seen, (int, float))
            or not 0 <= now - last_seen < IDLE_SECONDS
            or not isinstance(grant.get("version"), str)
            or not hmac.compare_digest(grant["version"], auth_version(credential.password_hash))):
        lock_collection(request)
        return False
    if touch:
        request.session[SESSION_KEY] = {**grant, "last_seen": now}
    return True


def unlock_collection(request, credential):
    request.session.cycle_key()
    rotate_token(request)
    request.session[SESSION_KEY] = {
        "student_id": credential.student_id,
        "version": auth_version(credential.password_hash),
        "last_seen": timezone.now().timestamp(),
    }


def set_initial_password(student, password):
    # Serialize first-time setup across browsers without ever replacing a password.
    encoded = make_password(password)
    with transaction.atomic():
        Student.objects.select_for_update().get(pk=student.pk)
        if CollectionCredential.objects.filter(student=student).exists():
            return None
        return CollectionCredential.objects.create(student=student, password_hash=encoded)


def verify_collection_password(credential, password):
    return bool(credential and check_password(password, credential.password_hash))


def collection_unlocked_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if collection_unlocked(request, touch=True):
            return view(request, *args, **kwargs)
        url = reverse("tower:collection_login")
        if request.path.startswith("/api/"):
            return JsonResponse({"ok": False, "error": "collection_locked", "login_url": url}, status=403)
        target = request.get_full_path() if request.method == "GET" else reverse("tower:home")
        return redirect(url + "?" + urlencode({"next": target}))
    return wrapped


class CollectionVisitMiddleware:
    """A new lesson-page visit ends journal access; API polling does not."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.session.get(SESSION_KEY) and not request.path.startswith(("/tower/", "/api/", "/static/"))
                and request.method == "GET"
                and request.headers.get("Sec-Fetch-Dest", "document") == "document"
                and request.headers.get("X-Requested-With") != "XMLHttpRequest"):
            lock_collection(request)
        return self.get_response(request)
