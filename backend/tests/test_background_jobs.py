import threading
import uuid

from services.background_jobs import (
    active_background_job_count,
    start_background_job,
    wait_for_background_jobs,
)


def test_background_job_registry_waits_for_started_job():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def job():
        started.set()
        release.wait(timeout=1.0)
        finished.set()

    thread = start_background_job(f"pytest:{uuid.uuid4()}", job, name="pytest-background-job")
    try:
        assert started.wait(timeout=1.0)
        assert active_background_job_count() >= 1
        assert wait_for_background_jobs(timeout=0.01) is False
        release.set()
        assert wait_for_background_jobs(timeout=1.0) is True
        thread.join(timeout=1.0)
        assert finished.is_set()
    finally:
        release.set()
        thread.join(timeout=1.0)
