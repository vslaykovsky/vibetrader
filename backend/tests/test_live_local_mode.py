from __future__ import annotations

import importlib.util
import os
import shutil
import time
import uuid
from pathlib import Path

import jwt

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vibetrader_flask_app", _ROOT / "app.py")
assert _spec and _spec.loader
_flask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_flask)
create_app = _flask.create_app


def _auth_headers() -> dict[str, str]:
    secret = os.environ["SUPABASE_JWT_SECRET"]
    tok = jwt.encode(
        {
            "sub": "live-local-test-user",
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {tok}"}


def _workspace(thread_id: str) -> Path:
    from services.agent import STRATEGIES_DIR

    return Path(STRATEGIES_DIR) / thread_id


def test_live_start_without_kubernetes_service_host_uses_local_runner():
    prev_backend = os.environ.get("LIVE_RUNNER_BACKEND")
    prev_k8s = os.environ.get("KUBERNETES_SERVICE_HOST")
    prev_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ.pop("LIVE_RUNNER_BACKEND", None)
    os.environ.pop("KUBERNETES_SERVICE_HOST", None)
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    thread_id = str(uuid.uuid4())
    ws = _workspace(thread_id)
    try:
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "strategy.py").write_text("x = 1\n", encoding="utf-8")
        app = create_app()
        client = app.test_client()
        res = client.post(
            "/live/start",
            json={"thread_id": thread_id, "paper": True},
            headers=_auth_headers(),
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body.get("runner_backend") == "local"
        assert body.get("deployment") is None
        assert body.get("ok") is True
    finally:
        shutil.rmtree(ws, ignore_errors=True)
        if prev_backend is not None:
            os.environ["LIVE_RUNNER_BACKEND"] = prev_backend
        else:
            os.environ.pop("LIVE_RUNNER_BACKEND", None)
        if prev_k8s is not None:
            os.environ["KUBERNETES_SERVICE_HOST"] = prev_k8s
        else:
            os.environ.pop("KUBERNETES_SERVICE_HOST", None)
        if prev_secret is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev_secret
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


def test_live_start_with_local_runner_backend():
    prev_backend = os.environ.get("LIVE_RUNNER_BACKEND")
    prev_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["LIVE_RUNNER_BACKEND"] = "local"
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    thread_id = str(uuid.uuid4())
    ws = _workspace(thread_id)
    try:
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "strategy.py").write_text("x = 1\n", encoding="utf-8")
        app = create_app()
        client = app.test_client()
        res = client.post(
            "/live/start",
            json={"thread_id": thread_id, "paper": True},
            headers=_auth_headers(),
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body.get("runner_backend") == "local"
        assert body.get("deployment") is None
        assert body.get("ok") is True
        run_id = str(body.get("run_id") or "").strip()
        assert run_id
        from db.models import LiveRun
        from db.session import SessionLocal

        session = SessionLocal()
        try:
            row = session.get(LiveRun, run_id)
            assert row is not None
            assert row.runner_backend == "local"
            assert row.created_by == "live-local-test-user"
        finally:
            session.close()
    finally:
        shutil.rmtree(ws, ignore_errors=True)
        if prev_backend is not None:
            os.environ["LIVE_RUNNER_BACKEND"] = prev_backend
        else:
            os.environ.pop("LIVE_RUNNER_BACKEND", None)
        if prev_secret is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev_secret
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


def test_live_status_skips_kubernetes_when_local_backend():
    prev_backend = os.environ.get("LIVE_RUNNER_BACKEND")
    prev_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["LIVE_RUNNER_BACKEND"] = "local"
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    thread_id = str(uuid.uuid4())
    ws = _workspace(thread_id)
    try:
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "strategy.py").write_text("x = 1\n", encoding="utf-8")
        app = create_app()
        client = app.test_client()
        hdrs = _auth_headers()
        res0 = client.post("/live/start", json={"thread_id": thread_id, "paper": True}, headers=hdrs)
        run_id = str(res0.get_json().get("run_id") or "").strip()
        res = client.get(f"/live/status?run_id={run_id}", headers=hdrs)
        assert res.status_code == 200
        body = res.get_json()
        assert body.get("ok") is True
        assert body.get("db", {}).get("runner_backend") == "local"
        k8s = body.get("k8s") or {}
        assert k8s.get("skipped") is True
        assert k8s.get("deployment_exists") is False
    finally:
        shutil.rmtree(ws, ignore_errors=True)
        if prev_backend is not None:
            os.environ["LIVE_RUNNER_BACKEND"] = prev_backend
        else:
            os.environ.pop("LIVE_RUNNER_BACKEND", None)
        if prev_secret is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev_secret
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


def test_live_stop_without_kubernetes_when_local_backend():
    prev_backend = os.environ.get("LIVE_RUNNER_BACKEND")
    prev_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["LIVE_RUNNER_BACKEND"] = "local"
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    thread_id = str(uuid.uuid4())
    ws = _workspace(thread_id)
    try:
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "strategy.py").write_text("x = 1\n", encoding="utf-8")
        app = create_app()
        client = app.test_client()
        hdrs = _auth_headers()
        res0 = client.post("/live/start", json={"thread_id": thread_id, "paper": True}, headers=hdrs)
        run_id = str(res0.get_json().get("run_id") or "").strip()
        res = client.post("/live/stop", json={"run_id": run_id}, headers=hdrs)
        assert res.status_code == 200
        body = res.get_json()
        assert body.get("deleted") is False
        from db.models import LiveRun
        from db.session import SessionLocal

        session = SessionLocal()
        try:
            row = session.get(LiveRun, run_id)
            assert row is not None
            assert row.status == "stopped"
        finally:
            session.close()
    finally:
        shutil.rmtree(ws, ignore_errors=True)
        if prev_backend is not None:
            os.environ["LIVE_RUNNER_BACKEND"] = prev_backend
        else:
            os.environ.pop("LIVE_RUNNER_BACKEND", None)
        if prev_secret is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev_secret
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
