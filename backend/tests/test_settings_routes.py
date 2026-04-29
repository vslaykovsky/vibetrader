from __future__ import annotations

import importlib.util
import os
import time
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
            "sub": "settings-test-user",
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {tok}"}


def test_settings_trading_get_requires_auth():
    app = create_app()
    client = app.test_client()
    response = client.get("/settings/trading")
    assert response.status_code == 401


def test_settings_trading_get_returns_503_when_supabase_service_not_configured():
    prev_secret = os.environ.get("SUPABASE_JWT_SECRET")
    prev_url = os.environ.get("SUPABASE_URL")
    prev_sr = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-settings-secret-32-chars-minimum!!"
    os.environ.pop("SUPABASE_URL", None)
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    try:
        app = create_app()
        client = app.test_client()
        response = client.get("/settings/trading", headers=_auth_headers())
        assert response.status_code == 503
    finally:
        if prev_secret is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev_secret
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
        if prev_url is not None:
            os.environ["SUPABASE_URL"] = prev_url
        else:
            os.environ.pop("SUPABASE_URL", None)
        if prev_sr is not None:
            os.environ["SUPABASE_SERVICE_ROLE_KEY"] = prev_sr
        else:
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)


def test_settings_trading_profile_put_requires_auth():
    app = create_app()
    client = app.test_client()
    response = client.put("/settings/trading/profile", json={"alpaca_api_key": "x"})
    assert response.status_code == 401
