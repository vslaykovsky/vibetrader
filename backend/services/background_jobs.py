from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

_condition = threading.Condition()
_active_jobs: dict[str, threading.Thread] = {}


def start_background_job(
    job_id: str,
    target: Callable[..., Any],
    *args: Any,
    name: str | None = None,
    daemon: bool = False,
    **kwargs: Any,
) -> threading.Thread:
    key = str(job_id or "").strip()
    if not key:
        raise ValueError("job_id is required")

    def run() -> None:
        try:
            target(*args, **kwargs)
        finally:
            with _condition:
                _active_jobs.pop(key, None)
                _condition.notify_all()

    thread = threading.Thread(target=run, name=name or key, daemon=daemon)
    with _condition:
        _active_jobs[key] = thread
    try:
        thread.start()
    except Exception:
        with _condition:
            _active_jobs.pop(key, None)
            _condition.notify_all()
        raise
    return thread


def active_background_job_count() -> int:
    with _condition:
        return len(_active_jobs)


def wait_for_background_jobs(timeout: float | None = None) -> bool:
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    with _condition:
        while _active_jobs:
            if deadline is None:
                _condition.wait()
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _condition.wait(remaining)
        return True
