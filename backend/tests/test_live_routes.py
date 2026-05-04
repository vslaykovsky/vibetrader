from __future__ import annotations

import importlib.util
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jwt

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vibetrader_flask_app", _ROOT / "app.py")
assert _spec and _spec.loader
_flask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_flask)
create_app = _flask.create_app

from api.live_routes import _utc_isoformat


def _auth_headers() -> dict[str, str]:
    secret = os.environ["SUPABASE_JWT_SECRET"]
    tok = jwt.encode(
        {
            "sub": "live-routes-test-user",
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {tok}"}


def test_utc_isoformat_marks_naive_datetimes_as_utc():
    assert _utc_isoformat(datetime(2026, 5, 3, 19, 10, 0)) == "2026-05-03T19:10:00Z"
    assert _utc_isoformat(datetime(2026, 5, 3, 19, 10, 0, tzinfo=timezone.utc)) == "2026-05-03T19:10:00Z"


def test_live_stream_requires_auth():
    app = create_app()
    client = app.test_client()
    response = client.get("/live/stream?run_id=00000000-0000-4000-8000-000000000001")
    assert response.status_code == 401


def test_live_start_requires_auth():
    app = create_app()
    client = app.test_client()
    response = client.post(
        "/live/start",
        json={"thread_id": "00000000-0000-4000-8000-000000000001", "paper": True},
    )
    assert response.status_code == 401


def test_live_stop_requires_auth():
    app = create_app()
    client = app.test_client()
    response = client.post(
        "/live/stop",
        json={"run_id": "00000000-0000-4000-8000-000000000001"},
    )
    assert response.status_code == 401


def test_live_status_requires_auth():
    app = create_app()
    client = app.test_client()
    response = client.get("/live/status?run_id=00000000-0000-4000-8000-000000000001")
    assert response.status_code == 401


def test_live_runs_requires_auth():
    app = create_app()
    client = app.test_client()
    response = client.get("/live/runs")
    assert response.status_code == 401


def test_live_delete_removes_stopped_run():
    prev_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    run_id = str(uuid.uuid4())
    running_run_id = str(uuid.uuid4())
    try:
        from db.models import LiveRun, LiveRunEvent, LiveRunOrder
        from db.session import SessionLocal

        session = SessionLocal()
        try:
            session.add(
                LiveRun(
                    id=run_id,
                    thread_id=str(uuid.uuid4()),
                    created_by="live-routes-test-user",
                    status="stopped",
                )
            )
            session.add(
                LiveRun(
                    id=running_run_id,
                    thread_id=str(uuid.uuid4()),
                    created_by="live-routes-test-user",
                    status="running",
                )
            )
            session.add(LiveRunEvent(run_id=run_id, kind="status", payload={"status": "stopped"}))
            session.add(LiveRunOrder(run_id=run_id, client_order_id="client-1"))
            session.commit()
        finally:
            session.close()

        app = create_app()
        client = app.test_client()
        running_response = client.delete(f"/live/runs/{running_run_id}", headers=_auth_headers())
        assert running_response.status_code == 409

        response = client.delete(f"/live/runs/{run_id}", headers=_auth_headers())
        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "run_id": run_id, "deleted": True}

        session = SessionLocal()
        try:
            assert session.get(LiveRun, running_run_id) is not None
            assert session.get(LiveRun, run_id) is None
            assert session.query(LiveRunEvent).filter_by(run_id=run_id).count() == 0
            assert session.query(LiveRunOrder).filter_by(run_id=run_id).count() == 0
        finally:
            session.close()
    finally:
        if prev_secret is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev_secret
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)

