import logging

from django.core.exceptions import RequestDataTooBig, SuspiciousOperation
from django.http import Http404, JsonResponse
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin

from .security import client_ip, request_is_limited


logger = logging.getLogger(__name__)

CSP_POLICY = "; ".join(
    [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "connect-src 'self' https://embed.diagrams.net",
        "frame-src https://www.youtube.com https://www.youtube-nocookie.com https://embed.diagrams.net",
        "media-src 'self' https:",
    ]
)


class SecurityResponseMiddleware(MiddlewareMixin):
    """Apply browser hardening and keep API failures machine-readable."""

    def process_request(self, request):
        if request.path == "/admin/login/" and request.method == "POST":
            if request_is_limited(
                "admin_login",
                client_ip(request),
                limit=int(getattr(settings, "ADMIN_LOGIN_ATTEMPT_LIMIT", 20)),
                window_seconds=900,
            ):
                response = JsonResponse(
                    {"ok": False, "error": "too many login attempts"},
                    status=429,
                )
                response["Retry-After"] = "900"
                return response
        return None

    def process_response(self, request, response):
        if request.path.startswith("/api/"):
            content_type = response.get("Content-Type", "")
            if response.status_code >= 400 and "application/json" not in content_type:
                response = JsonResponse(
                    {"ok": False, "error": "request failed"},
                    status=response.status_code,
                )
            response["Cache-Control"] = "no-store"

        response.setdefault("Content-Security-Policy", CSP_POLICY)
        response.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        response.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        return response

    def process_exception(self, request, exception):
        if not request.path.startswith("/api/"):
            return None
        if isinstance(exception, Http404):
            return JsonResponse({"ok": False, "error": "resource not found"}, status=404)
        if isinstance(exception, RequestDataTooBig):
            return JsonResponse({"ok": False, "error": "request body is too large"}, status=413)
        if isinstance(exception, SuspiciousOperation):
            return JsonResponse({"ok": False, "error": "invalid request"}, status=400)
        logger.error(
            "Unhandled API exception",
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        return JsonResponse(
            {"ok": False, "error": "internal server error"},
            status=500,
        )


def csrf_failure(request, reason=""):
    if request.path.startswith("/api/"):
        return JsonResponse({"ok": False, "error": "CSRF verification failed"}, status=403)
    from django.views.csrf import csrf_failure as django_csrf_failure

    return django_csrf_failure(request, reason=reason)