from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vibetrader_flask_app", _ROOT / "app.py")
assert _spec and _spec.loader
_flask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_flask)
create_app = _flask.create_app


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

