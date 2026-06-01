from __future__ import annotations

import os

from services.background_jobs import active_background_job_count, wait_for_background_jobs

bind = f"0.0.0.0:{os.getenv('PORT', '8080')}"
workers = int(os.getenv("WEB_CONCURRENCY", "8"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))
worker_class = "gthread"
timeout = int(os.getenv("GUNICORN_TIMEOUT", "1800"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "1860"))
keepalive = int(os.getenv("GUNICORN_KEEP_ALIVE", "5"))


def _shutdown_wait_seconds() -> float:
    raw = os.getenv("BACKGROUND_JOB_SHUTDOWN_TIMEOUT_SECONDS", "1800").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1800.0


def worker_exit(server, worker) -> None:
    count = active_background_job_count()
    if count <= 0:
        return
    wait_seconds = _shutdown_wait_seconds()
    worker.log.info("waiting for %s background job(s) before worker exit", count)
    drained = wait_for_background_jobs(timeout=wait_seconds)
    if drained:
        worker.log.info("background jobs finished before worker exit")
        return
    worker.log.warning(
        "timed out waiting for background jobs before worker exit; active_jobs=%s",
        active_background_job_count(),
    )
