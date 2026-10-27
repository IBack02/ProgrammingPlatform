from urllib.parse import urlsplit

from django import forms
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_http_methods

from core.models import Student
from core.security import clear_login_identity, login_retry_after, record_login_failure
from core.ui_translations import UI_TRANSLATIONS, get_ui_lang
from .collection_security import (collection_unlocked, lock_collection, set_initial_password,
                                  unlock_collection, verify_collection_password)
from .models import CollectionCredential, TowerAuditEvent
from .permissions import tower_student_required, tower_teacher_required
from .wallet_views import wallet_errors


class CollectionPasswordForm(forms.Form):
    password = forms.CharField(strip=False, max_length=15, widget=forms.PasswordInput(attrs={
        "minlength": 6, "maxlength": 15, "autocomplete": "current-password", "autofocus": True,
    }))
    confirmation = forms.CharField(strip=False, required=False, max_length=15, widget=forms.PasswordInput(attrs={
        "minlength": 6, "maxlength": 15, "autocomplete": "new-password",
    }))

    def __init__(self, *args, setup=False, text, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup, self.text = setup, text
        for field in self.fields.values():
            field.error_messages.update(required=text["journal_password_required"], max_length=text["journal_password_rules"])
        if setup:
            self.fields["password"].widget.attrs["autocomplete"] = "new-password"
            self.fields["password"].widget.attrs["aria-describedby"] = "password-rules"
            self.fields["confirmation"].required = True
        else:
            self.fields.pop("confirmation")

    def clean(self):
        values = super().clean()
        password = values.get("password")
        if self.setup and password is not None:
            if (not 6 <= len(password) <= 15 or not any(char.isalpha() for char in password)
                    or any(char.isspace() or not char.isprintable() for char in password)):
                self.add_error("password", self.text["journal_password_rules"])
            if password != values.get("confirmation"):
                self.add_error("confirmation", self.text["journal_password_mismatch"])
        return values


def _next_url(request):
    target = request.POST.get("next", request.GET.get("next", ""))
    try:
        parsed = urlsplit(target)
    except ValueError:
        return reverse("tower:home")
    # Return only to the journal's GET page, never arbitrary same-site endpoints.
    if not parsed.scheme and not parsed.netloc and parsed.path == reverse("tower:home") and "\\" not in target:
        return target
    return reverse("tower:home")


@require_http_methods(["GET", "POST"])
@tower_student_required
@ensure_csrf_cookie
@sensitive_post_parameters("password", "confirmation")
def login(request):
    if request.method == "POST":
        length = request.META.get("CONTENT_LENGTH", "0") or "0"
        if not length.isdigit() or len(length) > 7 or int(length) > 4096:
            return HttpResponseBadRequest()
    if collection_unlocked(request):
        return redirect(_next_url(request))
    text = UI_TRANSLATIONS[get_ui_lang(request)]
    student = request.tower_student
    credential = CollectionCredential.objects.filter(student=student).first()
    setup = credential is None
    form = CollectionPasswordForm(request.POST if request.method == "POST" else None, setup=setup, text=text)
    status, retry_after = 200, 0
    if request.method == "POST":
        retry_after = login_retry_after("collection", request, str(student.pk))
        if not retry_after and form.is_valid():
            password = form.cleaned_data["password"]
            if setup:
                credential = set_initial_password(student, password)
                accepted = credential is not None
            else:
                accepted = verify_collection_password(credential, password)
            if accepted:
                clear_login_identity("collection", request, str(student.pk))
                unlock_collection(request, credential)
                return redirect(_next_url(request))
            record_login_failure("collection", request, str(student.pk))
            retry_after = login_retry_after("collection", request, str(student.pk))
            form.add_error(None, text["journal_password_invalid"])
            status = 400
        elif not retry_after:
            status = 400
        if retry_after:
            form.add_error(None, text["journal_password_locked"].replace("{seconds}", str(retry_after)))
            status = 429
    response = render(request, "tower/collection_login.html", {
        "password_form": form, "password_setup": setup, "next_url": _next_url(request),
        "collection_student": student,
    }, status=status)
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


@require_POST
@tower_student_required
def lock(request):
    lock_collection(request)
    return redirect("tower:collection_login")


@require_http_methods(["GET", "POST"])
@tower_teacher_required
@wallet_errors
def reset(request, student_id):
    from .wallet_views import _scope
    from .wallet_views import positive_id
    group, students = _scope(request, positive_id(request.POST.get("class_id", request.GET.get("class_id"))))
    student = get_object_or_404(students, pk=student_id)
    if request.method == "POST":
        with transaction.atomic():
            Student.objects.select_for_update().get(pk=student.pk)
            deleted, _ = CollectionCredential.objects.filter(student=student).delete()
            if deleted:
                TowerAuditEvent.objects.create(event_type="collection_password_reset", student=student,
                    class_group=group, actor_teacher=request.tower_teacher,
                    actor_admin=request.user if request.user.is_authenticated and request.user.is_superuser else None,
                    metadata={})
        messages.success(request, UI_TRANSLATIONS[get_ui_lang(request)]["journal_password_reset_done"])
        return redirect(reverse("tower:teacher_wallets") + f"?class_id={group.pk}&student_id={student.pk}")
    return render(request, "tower/collection_password_reset.html", {"reset_student": student, "reset_class": group})
