import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.test import Client, TestCase, override_settings
from django.urls import path

from core import error_reports
from core.models import ServerErrorReport, Student, ClassGroup
from core.security import auth_version


def _boom(request, **kwargs):
    raise RuntimeError("judge0 exploded")


def _upstream_502(request):
    return JsonResponse({"ok": False, "error": "AI assistant temporarily unavailable"}, status=502)


def _missing(request):
    raise Http404("nope")


def _slow(request):
    time.sleep(0.02)
    return JsonResponse({"ok": True})


urlpatterns = [
    path("api/test/boom/<int:item_id>/", _boom),
    path("api/test/upstream/", _upstream_502),
    path("api/test/missing/", _missing),
    path("page/test/boom/", _boom),
    path("api/test/slow/", _slow),
    path("api/client-error-report/", error_reports.client_error_report),
]


@override_settings(ROOT_URLCONF="core.tests_error_reports")
class ErrorReportMiddlewareTests(TestCase):
    def setUp(self):
        cache.clear()
        group = ClassGroup.objects.create(name="9A")
        self.student = Student.objects.create(full_name="Ученик Один", class_group=group, pin_hash="!")
        session = self.client.session
        session.update({
            "student_id": self.student.id, "student_name": self.student.full_name,
            "student_class_id": group.id, "student_auth_version": auth_version("!"),
        })
        session.save()

    def test_api_exception_is_stored_with_traceback_user_and_request_id(self):
        response = self.client.get("/api/test/boom/7/?q=secret", HTTP_CF_RAY="8abc-ALA")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "internal server error")
        report = ServerErrorReport.objects.get()
        self.assertEqual(report.source, "server_exception")
        self.assertEqual(report.status_code, 500)
        self.assertEqual(report.route, "api/test/boom/<int:item_id>/")
        self.assertEqual(report.exception_type, "builtins.RuntimeError")
        self.assertIn("judge0 exploded", report.traceback)
        self.assertEqual(report.user_role, "student")
        self.assertEqual(report.user_id, self.student.id)
        self.assertEqual(report.edge_ray_id, "8abc-ALA")
        self.assertEqual(report.request_id, response["X-Request-ID"])
        context = report.server_context
        self.assertEqual(context["request"]["query_keys"], ["q"])
        self.assertNotIn("secret", json.dumps(context))
        self.assertEqual(context["database"]["status"], "ok")
        self.assertIn("process", context)

    def test_html_page_exception_is_stored(self):
        client = Client(raise_request_exception=False)
        self.assertEqual(client.get("/page/test/boom/").status_code, 500)
        self.assertEqual(ServerErrorReport.objects.get().source, "server_exception")

    def test_upstream_502_response_is_stored_and_repeats_are_grouped(self):
        for _ in range(3):
            self.assertEqual(self.client.get("/api/test/upstream/").status_code, 502)
        report = ServerErrorReport.objects.get()
        self.assertEqual((report.source, report.status_code, report.occurrences), ("server_response", 502, 3))

    def test_resolved_report_starts_a_new_group(self):
        self.client.get("/api/test/upstream/")
        ServerErrorReport.objects.update(is_resolved=True)
        self.client.get("/api/test/upstream/")
        self.assertEqual(ServerErrorReport.objects.count(), 2)

    def test_not_found_is_not_reported(self):
        self.assertEqual(self.client.get("/api/test/missing/").status_code, 404)
        self.assertFalse(ServerErrorReport.objects.exists())

    def test_incoming_request_id_is_reused(self):
        response = self.client.get("/api/test/upstream/", HTTP_X_REQUEST_ID="edge-request-123")
        self.assertEqual(response["X-Request-ID"], "edge-request-123")
        self.assertEqual(ServerErrorReport.objects.get().request_id, "edge-request-123")

    @override_settings(ERROR_REPORT_SLOW_REQUEST_SECONDS=0.01)
    def test_slow_request_is_reported_before_it_becomes_a_502(self):
        self.assertEqual(self.client.get("/api/test/slow/").status_code, 200)
        report = ServerErrorReport.objects.get()
        self.assertEqual(report.source, "slow_request")
        self.assertGreaterEqual(report.duration_ms, 10)

    def test_recent_warning_logs_are_attached(self):
        import logging

        logging.getLogger("core.judge0").warning("Judge0 queue is full")
        self.client.get("/api/test/upstream/")
        logs = ServerErrorReport.objects.get().server_context["recent_logs"]
        self.assertTrue(any(row["message"] == "Judge0 queue is full" for row in logs))

    def test_database_failure_spools_and_next_report_drains(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(ERROR_REPORT_SPOOL_PATH=str(Path(folder) / "spool.jsonl")):
            with patch("core.error_reports._store", side_effect=RuntimeError("db down")):
                self.client.get("/api/test/upstream/")
            self.assertFalse(ServerErrorReport.objects.exists())
            self.assertTrue((Path(folder) / "spool.jsonl").exists())
            self.client.get("/api/test/boom/1/")
            self.assertEqual(ServerErrorReport.objects.count(), 2)
            spooled = ServerErrorReport.objects.get(source="server_response")
            self.assertTrue(spooled.server_context["spooled"])
            self.assertFalse((Path(folder) / "spool.jsonl").exists())

    def test_worker_timeout_records_hanging_request_and_thread_stacks(self):
        from django.test import RequestFactory

        from django.contrib.sessions.backends.db import SessionStore

        fake = RequestFactory().post("/api/student/task/5/submit")
        # Registered before SessionMiddleware runs; the view loads the session later.
        error_reports._register_request("hung-request-1", fake)
        fake.session = SessionStore()
        fake.session.update({"student_id": self.student.id, "student_name": self.student.full_name})
        try:
            report = error_reports.record_worker_timeout(using="default")
        finally:
            error_reports._finish_request("hung-request-1", 502, 0)
        self.assertEqual((report.source, report.status_code), ("worker_timeout", 502))
        self.assertEqual(report.path, "/api/student/task/5/submit")
        self.assertEqual(report.request_id, "hung-request-1")
        self.assertEqual((report.user_role, report.user_id), ("student", self.student.id))
        stacks = report.server_context["threads"]
        self.assertTrue(any("test_worker_timeout_records" in row["stack"] for row in stacks))


@override_settings(ROOT_URLCONF="core.tests_error_reports")
class ClientErrorReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        self.csrf = "a" * 32
        self.client.cookies["csrftoken"] = self.csrf

    def post(self, payload, **extra):
        return self.client.post(
            "/api/client-error-report/", json.dumps(payload), content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf, **extra,
        )

    def test_browser_502_is_stored_and_linked_to_server_report(self):
        Client().get("/api/test/upstream/", HTTP_X_REQUEST_ID="req-abcdef12")
        server_report = ServerErrorReport.objects.get()
        response = self.post({
            "status": 502, "method": "post", "url": "/api/test/upstream/?pin=123456",
            "page": "http://testserver/student/", "duration_ms": 30123,
            "request_id": "req-abcdef12",
            "response_headers": {"cf-ray": "8f00-ALA", "server": "cloudflare", "set-cookie": "sessionid=1"},
            "body_excerpt": "<html><h1>502 Bad Gateway</h1></html>", "count": 3,
        })
        self.assertEqual(response.status_code, 201)
        report = ServerErrorReport.objects.get(source="client_gateway")
        self.assertEqual((report.status_code, report.method, report.path), (502, "POST", "/api/test/upstream/"))
        self.assertEqual(report.edge_ray_id, "8f00-ALA")
        self.assertEqual(report.duration_ms, 30123)
        self.assertEqual(report.client_context["body_excerpt"], "502 Bad Gateway")
        self.assertNotIn("set-cookie", report.client_context["response_headers"])
        self.assertEqual(report.client_context["page"], "/student/")
        self.assertIn(server_report.id, [row["id"] for row in report.server_context["related_reports"]])

    def test_network_failure_is_accepted(self):
        response = self.post({"status": 0, "method": "GET", "url": "/api/student/dashboard", "error": "Failed to fetch"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ServerErrorReport.objects.get().client_context["error"], "Failed to fetch")

    def test_invalid_reports_are_rejected(self):
        self.assertEqual(self.post({"status": 200, "url": "/api/x/"}).status_code, 400)
        self.assertEqual(self.post({"status": 502, "url": "https://other.example/api/"}).status_code, 400)
        self.assertEqual(self.post({"status": 502}).status_code, 400)
        self.assertFalse(ServerErrorReport.objects.exists())

    def test_csrf_is_required(self):
        response = self.client.post("/api/client-error-report/", json.dumps({"status": 502, "url": "/x/"}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_reports_are_rate_limited(self):
        for _ in range(40):
            self.post({"status": 502, "url": "/api/x/"})
        self.assertEqual(self.post({"status": 502, "url": "/api/x/"}).status_code, 429)


class ErrorReportAdminTests(TestCase):
    def test_admin_change_page_shows_collected_logs(self):
        from django.contrib.auth.models import User

        admin = User.objects.create_superuser("root", "root@example.com", "pw-123456")
        self.client.force_login(admin)
        report = error_reports.record_error_report(
            "worker_timeout", 502, method="POST", path="/api/student/task/1/submit",
            server_context={"threads": [{"name": "MainThread", "thread_id": 1,
                                         "stack": 'File "django/core/handlers/base.py"\nFile "core/judge0_client.py", line 159, in wait_batch'}]},
        )
        listing = self.client.get("/admin/core/servererrorreport/")
        self.assertContains(listing, "/api/student/task/1/submit")
        self.assertContains(listing, "502/503/504: 1")
        detail = self.client.get(f"/admin/core/servererrorreport/{report.id}/change/")
        self.assertContains(detail, "wait_batch")
        self.client.post("/admin/core/servererrorreport/", {"action": "mark_resolved", "_selected_action": [report.id]})
        report.refresh_from_db()
        self.assertTrue(report.is_resolved)
        self.assertIsNotNone(report.resolved_at)
