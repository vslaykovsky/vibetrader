from __future__ import annotations

import importlib.util
import os
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
            "sub": "patch-name-test-user",
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {tok}"}


def test_patch_strategy_name():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    thread_id = str(uuid.uuid4())
    app = create_app()
    try:
        client = app.test_client()
        h = _auth_headers()
        g0 = client.get(f"/strategy?thread_id={thread_id}", headers=h)
        assert g0.status_code == 200
        body0 = g0.get_json()
        run_id = str(body0.get("id") or "").strip()
        assert run_id

        res = client.patch(
            "/strategy",
            json={"id": run_id, "strategy_name": "  Custom title  "},
            headers=h,
        )
        assert res.status_code == 200
        b = res.get_json()
        assert (b.get("strategy_name") or "") == "Custom title"

        g1 = client.get(f"/strategy?id={run_id}", headers=h)
        assert g1.status_code == 200
        b1 = g1.get_json()
        assert (b1.get("strategy_name") or "") == "Custom title"
    finally:
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
