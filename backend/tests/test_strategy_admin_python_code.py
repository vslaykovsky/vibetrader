from __future__ import annotations

import importlib.util
import os
import time
import uuid
from pathlib import Path

import jwt

from db.models import Strategy
from db.session import SessionLocal

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vibetrader_flask_app", _ROOT / "app.py")
assert _spec and _spec.loader
_flask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_flask)
create_app = _flask.create_app


def _auth_headers(email: str) -> dict[str, str]:
    secret = os.environ["SUPABASE_JWT_SECRET"]
    tok = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "email": email,
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {tok}"}


def test_strategy_response_includes_python_code_only_for_admin():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    thread_id = str(uuid.uuid4())
    code = "def run_strategy():\n    return {'ok': True}\n"
    session = SessionLocal()
    try:
        strategy = Strategy(
            thread_id=thread_id,
            messages=[],
            canvas={},
            code=code,
            status="success",
            status_text="",
        )
        session.add(strategy)
        session.commit()
        run_id = strategy.id
    finally:
        session.close()

    app = create_app()
    try:
        client = app.test_client()

        user_res = client.get(
            f"/strategy?id={run_id}",
            headers=_auth_headers("user@example.com"),
        )
        assert user_res.status_code == 200
        assert "python_code" not in user_res.get_json()

        admin_res = client.get(
            f"/strategy?id={run_id}",
            headers=_auth_headers("vslaykovsky@gmail.com"),
        )
        assert admin_res.status_code == 200
        assert admin_res.get_json().get("python_code") == code
    finally:
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
