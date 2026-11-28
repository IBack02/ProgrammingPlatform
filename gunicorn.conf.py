"""Gunicorn settings, loaded automatically from the project root.

The important part is ``worker_abort``: when a worker exceeds ``timeout`` the
master aborts it and the proxy (Render/Cloudflare) answers 502. Right before
the worker dies we store the stack of every thread and the request it was
serving in ``ServerErrorReport`` (Django admin -> "Отчёты об ошибках сервера").
"""

import os

# Command-line flags and GUNICORN_CMD_ARGS still override these values.
if os.environ.get("GUNICORN_TIMEOUT"):
    timeout = int(os.environ["GUNICORN_TIMEOUT"])
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))


def worker_abort(worker):
    try:
        from core.error_reports import record_worker_timeout

        record_worker_timeout(worker)
    except Exception:  # pragma: no cover - the worker is being killed anyway
        worker.log.exception("Could not record worker timeout report")
