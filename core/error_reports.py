"""Collect diagnostics for 502/503/504 failures and store them for admins.

A 502 is produced by the proxy (Render / Cloudflare), not by Django, so it is
captured from three sides and grouped into one ``ServerErrorReport`` table:

* ``ErrorReportMiddleware`` records every 5xx response, every unhandled
  exception and every request slow enough to approach the gunicorn timeout.
* ``record_worker_timeout`` is called from the gunicorn ``worker_abort`` hook
  (see ``gunicorn.conf.py``) right before a timed-out worker is killed - the
  exact moment the proxy starts answering 502. It stores the stacks of all
  threads, so the admin sees which line was hanging.
* ``client_error_report`` receives reports from ``core/static/core/error_reporter.js``
  when the browser gets 502/503/504 or a dropped connection for an API call.

Every report carries the same server context: process, request, the last
requests and WARNING+ log records of the worker, in-flight requests and a
database ping. When the database itself is unavailable the report is spooled
to a JSONL file and written on the next successful report.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connections
from django.db.models import F
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

PROCESS_STARTED_AT = time.time()
GATEWAY_STATUSES = frozenset({502, 503, 504})
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
_ID_SEGMENT_RE = re.compile(r"/\d+(?=/|$)")
_UUID_SEGMENT_RE = re.compile(r"/[0-9a-fA-F-]{32,36}(?=/|$)")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_SENSITIVE_HEADER_MARKERS = ("cookie", "authorization", "csrf", "token", "secret", "password")
_SAFE_HEADERS = (
    "HTTP_USER_AGENT", "HTTP_ACCEPT", "HTTP_REFERER", "HTTP_CF_RAY", "HTTP_RNDR_ID",
    "HTTP_X_REQUEST_ID", "HTTP_X_REQUEST_START", "CONTENT_TYPE", "CONTENT_LENGTH",
    "HTTP_CF_IPCOUNTRY", "HTTP_SEC_FETCH_MODE", "HTTP_SEC_FETCH_DEST",
)
_GROUP_WINDOW = timedelta(hours=24)
_ISOLATED_ALIAS = "error_reports_isolated"

_lock = threading.Lock()
_in_flight: dict[str, dict] = {}
_recent_requests: deque = deque(maxlen=40)
_recent_logs: deque = deque(maxlen=60)


# ---------------------------------------------------------------------------
# Per-process ring buffers
# ---------------------------------------------------------------------------

class RecentLogHandler(logging.Handler):
    """Keeps the last WARNING+ records of this worker for error reports."""

    def emit(self, record):
        if record.name.startswith(__name__):
            return
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed log call
            message = str(record.msg)
        entry = {
            "at": timezone.now().isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": message[:1000],
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = f"{record.exc_info[0].__name__}: {str(record.exc_info[1])[:300]}"
        with _lock:
            _recent_logs.append(entry)


def install_log_handler():
    root = logging.getLogger()
    if not any(isinstance(handler, RecentLogHandler) for handler in root.handlers):
        root.addHandler(RecentLogHandler(level=logging.WARNING))


def _register_request(request_id: str, request: HttpRequest):
    with _lock:
        _in_flight[request_id] = {
            "request_id": request_id,
            "method": request.method,
            "path": _clip(request.path, 300),
            "started": time.monotonic(),
            "started_at": timezone.now().isoformat(timespec="seconds"),
            "thread_id": threading.get_ident(),
            # The session is attached by inner middleware later; the user is
            # resolved when a report is built.
            "request": request,
        }


def _finish_request(request_id: str, status: int, duration_ms: int):
    with _lock:
        row = _in_flight.pop(request_id, None)
        if row:
            _recent_requests.append({
                "at": timezone.now().isoformat(timespec="seconds"),
                "method": row["method"],
                "path": row["path"],
                "status": status,
                "duration_ms": duration_ms,
            })


def _in_flight_snapshot(exclude: str = "") -> list[dict]:
    now = time.monotonic()
    with _lock:
        rows = [dict(row) for key, row in _in_flight.items() if key != exclude]
    for row in rows:
        row["elapsed_ms"] = int((now - row.pop("started")) * 1000)
        row["user"] = _user_summary(row.pop("request", None), touch_session=False)
    return sorted(rows, key=lambda row: -row["elapsed_ms"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clip(value, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def normalize_path(path: str) -> str:
    path = _UUID_SEGMENT_RE.sub("/<uuid>", path or "")
    return _ID_SEGMENT_RE.sub("/<id>", path)


def new_request_id() -> str:
    return uuid.uuid4().hex


def request_id_for(request: HttpRequest) -> str:
    existing = getattr(request, "error_report_id", "")
    if existing:
        return existing
    incoming = str(request.META.get("HTTP_X_REQUEST_ID") or "")
    request_id = incoming if REQUEST_ID_RE.match(incoming) else new_request_id()
    request.error_report_id = request_id
    return request_id


def _user_summary(request: HttpRequest | None, *, touch_session: bool = True) -> dict:
    """Who made the request. Never raises: the session store may be down."""

    if request is None:
        return {}
    try:
        session = getattr(request, "session", None)
        if session is not None and (touch_session or getattr(session, "_session_cache", None) is not None):
            if session.get("teacher_id"):
                return {"role": "teacher", "id": session.get("teacher_id"), "label": session.get("teacher_name", "")}
            if session.get("student_id"):
                return {"role": "student", "id": session.get("student_id"), "label": session.get("student_name", "")}
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            return {"role": "staff" if user.is_staff else "user", "id": user.pk, "label": user.get_username()}
    except Exception:
        return {"role": "unknown"}
    return {"role": "anonymous"}


def _request_context(request: HttpRequest) -> dict:
    headers = {}
    for key in _SAFE_HEADERS:
        value = request.META.get(key)
        if not value:
            continue
        name = key.removeprefix("HTTP_").lower().replace("_", "-")
        if name == "referer":
            value = urlsplit(str(value)).path
        headers[name] = _clip(value, 300)
    resolver = getattr(request, "resolver_match", None)
    return {
        "method": request.method,
        "path": _clip(request.path, 500),
        "route": getattr(resolver, "route", "") or "",
        "view": getattr(resolver, "view_name", "") or "",
        # Values may contain names or PINs: keep only the parameter names.
        "query_keys": sorted(request.GET.keys())[:30],
        "headers": {key: value for key, value in headers.items()
                    if not any(marker in key for marker in _SENSITIVE_HEADER_MARKERS)},
        "forwarded_hops": len([item for item in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if item.strip()]),
    }


def _db_probe(alias: str = "default") -> dict:
    connection = connections[alias]
    result = {"vendor": connection.vendor, "in_atomic_block": bool(connection.in_atomic_block)}
    if connection.in_atomic_block and connection.needs_rollback:
        result["status"] = "transaction marked for rollback"
        return result
    started = time.monotonic()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        result["status"] = "ok"
    except Exception as exc:
        result["status"] = f"error: {type(exc).__name__}: {_clip(exc, 300)}"
    result["ping_ms"] = round((time.monotonic() - started) * 1000, 1)
    return result


def _memory_rss_mb():
    try:
        import resource  # Unix only

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB, macOS reports bytes.
        return round(usage / 1024 if sys.platform != "darwin" else usage / 1024 / 1024, 1)
    except Exception:
        return None


def collect_server_context(request: HttpRequest | None = None, *, request_id: str = "",
                           include_threads: bool = False, probe_db: bool = True,
                           db_alias: str = "default") -> dict:
    """Snapshot of everything an admin needs to understand a 502."""

    context = {
        "collected_at": timezone.now().isoformat(timespec="seconds"),
        "process": {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "uptime_s": int(time.time() - PROCESS_STARTED_AT),
            "python": platform.python_version(),
            "threads": threading.active_count(),
            "max_rss_mb": _memory_rss_mb(),
            "load_avg": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        },
        "deploy": {
            key.lower(): os.environ[key]
            for key in ("RENDER_SERVICE_NAME", "RENDER_INSTANCE_ID", "RENDER_GIT_COMMIT",
                        "WEB_CONCURRENCY", "GUNICORN_TIMEOUT", "GUNICORN_CMD_ARGS")
            if os.environ.get(key)
        },
        "settings": {
            "debug": bool(settings.DEBUG),
            "slow_request_seconds": slow_request_threshold(),
            "db_conn_max_age": settings.DATABASES.get("default", {}).get("CONN_MAX_AGE"),
        },
        "in_flight": _in_flight_snapshot(exclude=request_id)[:15],
    }
    with _lock:
        context["recent_requests"] = list(_recent_requests)[-25:]
        context["recent_logs"] = list(_recent_logs)[-30:]
    if request is not None:
        context["request"] = _request_context(request)
    if probe_db:
        try:
            context["database"] = _db_probe(db_alias)
        except Exception as exc:
            context["database"] = {"status": f"probe failed: {type(exc).__name__}"}
    if include_threads:
        context["threads"] = thread_stacks()
    return context


def thread_stacks(limit: int = 40) -> list[dict]:
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    stacks = []
    for thread_id, frame in sys._current_frames().items():
        lines = traceback.format_stack(frame, limit=limit)
        stacks.append({
            "thread_id": thread_id,
            "name": names.get(thread_id, "?"),
            "stack": "".join(lines)[-12000:],
        })
    return stacks


def slow_request_threshold() -> float:
    return float(getattr(settings, "ERROR_REPORT_SLOW_REQUEST_SECONDS", 20))


def _fingerprint(source: str, status_code: int, method: str, route: str, path: str, exception_type: str) -> str:
    key = "|".join([source, str(status_code), method.upper(), route or normalize_path(path), exception_type])
    return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()


def _spool_path() -> Path:
    configured = getattr(settings, "ERROR_REPORT_SPOOL_PATH", "")
    return Path(configured) if configured else Path(tempfile.gettempdir()) / "progaplatform-error-reports.jsonl"


def _spool_write(payload: dict):
    try:
        path = _spool_path()
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            return  # Cap disk usage during long outages.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("Could not spool error report")


def drain_spool(using: str = "default") -> int:
    """Write reports spooled while the database was unavailable."""

    path = _spool_path()
    if not path.exists():
        return 0
    processing = path.with_suffix(f".{os.getpid()}.draining")
    try:
        path.replace(processing)
    except OSError:
        return 0  # Another worker is draining it.
    written = 0
    try:
        for line in processing.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            payload.setdefault("server_context", {})["spooled"] = True
            if _store(payload, using=using) is not None:
                written += 1
    finally:
        processing.unlink(missing_ok=True)
    return written


def _store(payload: dict, *, using: str = "default"):
    from .models import ServerErrorReport

    now = timezone.now()
    seen_at = payload.pop("seen_at", None) or now
    if isinstance(seen_at, str):
        try:
            from django.utils.dateparse import parse_datetime

            seen_at = parse_datetime(seen_at) or now
        except Exception:
            seen_at = now
    manager = ServerErrorReport.objects.using(using)
    existing = (
        manager.filter(fingerprint=payload["fingerprint"], is_resolved=False,
                       last_seen_at__gte=now - _GROUP_WINDOW)
        .order_by("-last_seen_at").first()
    )
    if existing:
        updates = {key: value for key, value in payload.items()
                   if key not in {"fingerprint", "source", "status_code", "method"}}
        manager.filter(pk=existing.pk).update(
            occurrences=F("occurrences") + 1, last_seen_at=seen_at, **updates,
        )
        existing.refresh_from_db(using=using)
        return existing
    return manager.create(first_seen_at=seen_at, last_seen_at=seen_at, **payload)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_error_report(source: str, status_code: int, request: HttpRequest | None = None, *,
                        exc_info=None, duration_ms: int | None = None,
                        client_context: dict | None = None, server_context: dict | None = None,
                        method: str = "", path: str = "", route: str = "", request_id: str = "",
                        edge_ray_id: str = "", user: dict | None = None, using: str = "default"):
    """Gather logs for a failed request and store them for the admin.

    Returns the stored ``ServerErrorReport`` or ``None`` when it had to be spooled.
    Never raises: diagnostics must not turn into a second failure.
    """

    try:
        if request is not None:
            request_id = request_id or request_id_for(request)
            method = method or request.method or ""
            path = path or request.path or ""
            resolver = getattr(request, "resolver_match", None)
            route = route or (getattr(resolver, "route", "") or "")
            edge_ray_id = edge_ray_id or request.META.get("HTTP_CF_RAY", "") or request.META.get("HTTP_RNDR_ID", "")
        user = user if user is not None else _user_summary(request)
        exception_type = exception_message = trace = ""
        if exc_info and exc_info[0] is not None:
            exception_type = f"{exc_info[0].__module__}.{exc_info[0].__qualname__}"
            exception_message = _clip(exc_info[1], 4000)
            trace = "".join(traceback.format_exception(*exc_info))[-20000:]
        if server_context is None:
            server_context = collect_server_context(request, request_id=request_id, db_alias=using)
        user_agent = request.META.get("HTTP_USER_AGENT", "") if request is not None else ""
        payload = {
            "fingerprint": _fingerprint(source, status_code, method, route, path, exception_type),
            "source": source,
            "status_code": int(status_code),
            "method": _clip(method.upper(), 10),
            "path": _clip(path, 500),
            "route": _clip(route, 200),
            "request_id": _clip(request_id, 64),
            "edge_ray_id": _clip(edge_ray_id, 64),
            "user_role": _clip(user.get("role", ""), 16),
            "user_id": user.get("id") if isinstance(user.get("id"), int) else None,
            "user_label": _clip(user.get("label", ""), 160),
            "user_agent": _clip(user_agent or (client_context or {}).get("user_agent", ""), 300),
            "duration_ms": duration_ms,
            "exception_type": exception_type,
            "exception_message": exception_message,
            "traceback": trace,
            "server_context": json.loads(json.dumps(server_context, default=str)),
            "client_context": json.loads(json.dumps(client_context or {}, default=str)),
        }
    except Exception:
        logger.exception("Could not build error report")
        return None

    try:
        report = _store(dict(payload), using=using)
    except Exception:
        logger.exception("Could not store error report; spooling it")
        payload["seen_at"] = timezone.now().isoformat()
        _spool_write(payload)
        return None
    try:
        drain_spool(using=using)
    except Exception:
        logger.exception("Could not drain spooled error reports")
    return report


def _isolated_alias() -> str:
    """A fresh connection that does not share the request's (possibly locked) transaction."""

    if _ISOLATED_ALIAS not in connections.settings:
        config = dict(connections.settings["default"])
        config["CONN_MAX_AGE"] = 0
        config["ATOMIC_REQUESTS"] = False
        if config.get("ENGINE", "").endswith("postgresql"):
            config["OPTIONS"] = {**config.get("OPTIONS", {}), "connect_timeout": 5}
        connections.settings[_ISOLATED_ALIAS] = config
    return _ISOLATED_ALIAS


def record_worker_timeout(worker=None, *, using: str | None = None):
    """Called by gunicorn ``worker_abort``: the worker timed out and the proxy answers 502."""

    alias = using or _isolated_alias()
    try:
        in_flight = _in_flight_snapshot()
        primary = in_flight[0] if in_flight else {}
        context = collect_server_context(include_threads=True, probe_db=True, db_alias=alias)
        context["gunicorn"] = {
            "timeout_s": getattr(getattr(worker, "cfg", None), "timeout", None),
            "worker_class": getattr(getattr(worker, "cfg", None), "worker_class_str", None),
            "worker_pid": getattr(worker, "pid", os.getpid()),
            "requests_handled": getattr(worker, "nr", None),
        }
        return record_error_report(
            "worker_timeout", 502,
            method=primary.get("method", ""), path=primary.get("path", ""),
            request_id=primary.get("request_id", ""), user=primary.get("user") or {},
            duration_ms=primary.get("elapsed_ms"), server_context=context, using=alias,
        )
    finally:
        if alias == _ISOLATED_ALIAS:
            try:
                connections[alias].close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Middleware and client endpoint
# ---------------------------------------------------------------------------

def remember_exception(request, exc_info):
    if request is not None and exc_info and exc_info[0] is not None:
        request._error_report_exc_info = exc_info


def _on_request_exception(sender, request=None, **kwargs):
    remember_exception(request, sys.exc_info())


class ErrorReportMiddleware:
    """Outermost middleware: request id, timing and recording of 5xx/slow requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request_id_for(request)
        started = time.monotonic()
        _register_request(request_id, request)
        status = 500
        try:
            response = self.get_response(request)
            status = response.status_code
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            _finish_request(request_id, status, duration_ms)

        # Http404 / PermissionDenied also pass through process_exception: only
        # failures that ended as 5xx are worth an admin report.
        exc_info = getattr(request, "_error_report_exc_info", None)
        if status >= 500:
            record_error_report(
                "server_exception" if exc_info else "server_response", status, request,
                exc_info=exc_info, duration_ms=duration_ms,
            )
        elif duration_ms >= slow_request_threshold() * 1000 and not request.path.startswith("/static/"):
            record_error_report("slow_request", status, request, duration_ms=duration_ms)
        response["X-Request-ID"] = request_id
        return response


def _client_text(value, limit: int) -> str:
    return _clip(_SPACE_RE.sub(" ", str(value or "")).strip(), limit)


def _same_origin_path(raw: str, host: str) -> str:
    try:
        parts = urlsplit(str(raw or ""))
    except ValueError:
        return ""
    if parts.netloc and parts.netloc.lower() != (host or "").lower():
        return ""
    return parts.path if parts.path.startswith("/") else ""


@require_POST
def client_error_report(request: HttpRequest):
    """Browser-side evidence of a 502/503/504 or a dropped API connection."""

    from .models import ServerErrorReport
    from .security import client_ip, request_is_limited

    if request_is_limited("client_error_report", client_ip(request), limit=40, window_seconds=600):
        return JsonResponse({"ok": False, "error": "too many reports"}, status=429)
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    try:
        status = int(data.get("status") or 0)
    except (TypeError, ValueError):
        status = -1
    if status != 0 and status not in GATEWAY_STATUSES and not 500 <= status <= 599:
        return JsonResponse({"ok": False, "error": "status must be 0 or 5xx"}, status=400)
    host = request.get_host()
    failed_path = _same_origin_path(data.get("url"), host)
    if not failed_path:
        return JsonResponse({"ok": False, "error": "url must be a same-origin path"}, status=400)
    method = _client_text(data.get("method") or "GET", 10).upper()
    if not re.fullmatch(r"[A-Z]{3,7}", method):
        method = "GET"
    response_headers = data.get("response_headers") if isinstance(data.get("response_headers"), dict) else {}
    safe_headers = {
        _client_text(key, 40).lower(): _client_text(value, 200)
        for key, value in list(response_headers.items())[:12]
        if not any(marker in str(key).lower() for marker in _SENSITIVE_HEADER_MARKERS)
    }
    failed_request_id = _client_text(data.get("request_id") or safe_headers.get("x-request-id"), 64)
    if failed_request_id and not REQUEST_ID_RE.match(failed_request_id):
        failed_request_id = ""
    body_excerpt = _client_text(_TAG_RE.sub(" ", str(data.get("body_excerpt") or "")), 1200)
    try:
        duration_ms = max(0, min(int(float(data.get("duration_ms") or 0)), 3_600_000))
    except (TypeError, ValueError):
        duration_ms = None
    try:
        repeat_count = max(1, min(int(data.get("count") or 1), 1000))
    except (TypeError, ValueError):
        repeat_count = 1
    client_context = {
        "page": _same_origin_path(data.get("page"), host),
        "error": _client_text(data.get("error"), 300),
        "body_excerpt": body_excerpt,
        "response_headers": safe_headers,
        "occurred_at": _client_text(data.get("occurred_at"), 40),
        "count_in_browser": repeat_count,
        "online": bool(data.get("online", True)),
        "visibility": _client_text(data.get("visibility"), 20),
        "connection": _client_text(data.get("connection"), 40),
        "retry_of_queue": bool(data.get("queued")),
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
    }

    since = timezone.now() - timedelta(minutes=10)
    related = ServerErrorReport.objects.filter(last_seen_at__gte=since).exclude(source="client_gateway")
    if failed_request_id:
        related = related.filter(request_id=failed_request_id) | related.filter(path=failed_path)
    else:
        related = related.filter(path=failed_path)
    server_context = collect_server_context(request, request_id=request_id_for(request))
    server_context["reported_by_browser"] = True
    server_context["related_reports"] = [
        {"id": row.id, "source": row.source, "status": row.status_code,
         "request_id": row.request_id, "last_seen_at": row.last_seen_at.isoformat()}
        for row in related.order_by("-last_seen_at")[:10]
    ]
    report = record_error_report(
        "client_gateway", status or 502, None,
        method=method, path=failed_path, request_id=failed_request_id,
        edge_ray_id=safe_headers.get("cf-ray", "") or safe_headers.get("rndr-id", ""),
        duration_ms=duration_ms, client_context=client_context, server_context=server_context,
        user=_user_summary(request),
    )
    return JsonResponse({"ok": True, "stored": report is not None}, status=201)
