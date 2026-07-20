from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import jwt
from flask import jsonify

from services.eula import (
    EULA_DOCUMENT,
    EULA_VERSION,
    EulaAcceptance,
    normalize_client_ip,
)


_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vibetrader_eula_app", _ROOT / "app.py")
assert _spec and _spec.loader
_flask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_flask)
create_app = _flask.create_app


def _auth_headers(user_id: str = "eula-test-user") -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": user_id,
            "email": "eula@example.com",
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_normalize_client_ip_accepts_forwarded_address_only():
    assert normalize_client_ip("203.0.113.7, 10.0.0.1") == "203.0.113.7"
    assert normalize_client_ip("2001:db8::1") == "2001:db8::1"
    assert normalize_client_ip("not-an-ip") is None
    assert normalize_client_ip("") is None


def test_eula_document_has_current_version_and_risk_sections():
    assert EULA_DOCUMENT["version"] == EULA_VERSION
    titles = [section["title"] for section in EULA_DOCUMENT["sections"]]
    assert any("no financial advice" in title.lower() for title in titles)
    assert any("live trading" in title.lower() for title in titles)
    assert any("risks" in title.lower() for title in titles)


def test_eula_get_returns_current_document(monkeypatch):
    previous_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-eula-secret-32-chars-minimum!!"
    acceptance = EulaAcceptance(False, "", None)
    monkeypatch.setattr("api.settings_routes.service_role_configured", lambda: True)
    monkeypatch.setattr("api.settings_routes.fetch_eula_acceptance", lambda _uid: acceptance)
    try:
        response = create_app().test_client().get("/eula", headers=_auth_headers())
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        payload = response.get_json()
        assert payload["agreement"]["version"] == EULA_VERSION
        assert payload["acceptance"] == acceptance.to_dict()
    finally:
        if previous_secret is None:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
        else:
            os.environ["SUPABASE_JWT_SECRET"] = previous_secret


def test_eula_accept_requires_all_acknowledgments_and_records_metadata(monkeypatch):
    previous_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-eula-secret-32-chars-minimum!!"
    captured: dict = {}

    def record(user_id, **kwargs):
        captured.update({"user_id": user_id, **kwargs})
        return EulaAcceptance(True, EULA_VERSION, "2026-07-19T20:30:00Z")

    monkeypatch.setattr("api.settings_routes.service_role_configured", lambda: True)
    monkeypatch.setattr("api.settings_routes.record_eula_acceptance", record)
    client = create_app().test_client()
    headers = {
        **_auth_headers("accepted-user"),
        "X-Forwarded-For": "203.0.113.8, 10.0.0.2",
        "User-Agent": "Eula Test Browser",
    }
    try:
        incomplete = client.post(
            "/eula/accept",
            headers=headers,
            json={"version": EULA_VERSION, "accepted": True},
        )
        assert incomplete.status_code == 400
        assert captured == {}

        response = client.post(
            "/eula/accept",
            headers=headers,
            json={
                "version": EULA_VERSION,
                "accepted": True,
                "age_confirmed": True,
                "risk_acknowledged": True,
            },
        )
        assert response.status_code == 200
        assert response.get_json()["acceptance"]["accepted"] is True
        assert captured == {
            "user_id": "accepted-user",
            "email": "eula@example.com",
            "client_ip": "203.0.113.8",
            "user_agent": "Eula Test Browser",
        }
    finally:
        if previous_secret is None:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
        else:
            os.environ["SUPABASE_JWT_SECRET"] = previous_secret


def test_protected_api_is_blocked_until_eula_is_accepted(monkeypatch):
    previous_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-eula-secret-32-chars-minimum!!"

    def denied(_user_id):
        return jsonify({"error": "EULA required", "code": "eula_required"}), 403

    monkeypatch.setattr("auth._eula_access_error", denied)
    try:
        response = create_app().test_client().get("/threads", headers=_auth_headers())
        assert response.status_code == 403
        assert response.get_json()["code"] == "eula_required"
    finally:
        if previous_secret is None:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
        else:
            os.environ["SUPABASE_JWT_SECRET"] = previous_secret
