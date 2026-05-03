from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vibetrader_flask_app", _ROOT / "app.py")
assert _spec and _spec.loader
_flask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_flask)
create_app = _flask.create_app

from api.live_routes import _utc_isoformat


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

