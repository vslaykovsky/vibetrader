from __future__ import annotations

import importlib.util
import os
import shutil
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import jwt

from db.models import Strategy
from db.session import SessionLocal

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vibetrader_impersonation_app", _ROOT / "app.py")
assert _spec and _spec.loader
_flask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_flask)
create_app = _flask.create_app


def _auth_headers(email: str, sub: str, *, act_as: str = "") -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": sub,
            "email": email,
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    if act_as:
        headers["X-Act-As-User"] = act_as
    return headers


def _strategy(*, thread_id: str, owner: str, email: str, created_at: datetime) -> Strategy:
    return Strategy(
        thread_id=thread_id,
        created_by=owner,
        created_by_email=email,
        messages=[{"role": "user", "content": "seed"}],
        canvas={},
        code="",
        status="success",
        status_text="",
        strategy_name="Seed strategy",
        created_at=created_at,
    )


def test_non_admin_cannot_impersonate_an_existing_user():
    previous_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-impersonation-secret-32-chars!!"
    target = f"impersonation-target-{uuid.uuid4()}"
    thread_id = str(uuid.uuid4())
    session = SessionLocal()
    try:
        session.add(
            _strategy(
                thread_id=thread_id,
                owner=target,
                email="target@example.com",
                created_at=datetime(2099, 2, 1, 12, 0, 0),
            )
        )
        session.commit()
    finally:
        session.close()

    app = create_app()
    try:
        response = app.test_client().get(
            "/threads",
            headers=_auth_headers("ordinary@example.com", f"ordinary-{uuid.uuid4()}", act_as=target),
        )
        assert response.status_code == 403
        assert response.get_json() == {"error": "forbidden"}
    finally:
        session = SessionLocal()
        try:
            session.query(Strategy).filter(Strategy.thread_id == thread_id).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()
        if previous_secret is None:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
        else:
            os.environ["SUPABASE_JWT_SECRET"] = previous_secret


def test_admin_impersonation_applies_target_identity_to_reads_and_writes(monkeypatch):
    previous_secret = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-impersonation-secret-32-chars!!"
    actor = f"impersonation-admin-{uuid.uuid4()}"
    target = f"impersonation-target-{uuid.uuid4()}"
    target_email = f"target-{uuid.uuid4()}@example.com"
    thread_id = str(uuid.uuid4())
    created_at = datetime(2099, 3, 1, 12, 0, 0)
    session = SessionLocal()
    try:
        session.add(
            _strategy(
                thread_id=thread_id,
                owner=target,
                email="old-target@example.com",
                created_at=created_at - timedelta(minutes=1),
            )
        )
        session.add(
            _strategy(
                thread_id=thread_id,
                owner=target,
                email=target_email,
                created_at=created_at,
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr("api.routes.start_background_job", lambda *args, **kwargs: None)
    app = create_app()
    workspace = _ROOT / "strategies_v2" / thread_id
    try:
        client = app.test_client()
        actor_headers = _auth_headers("vslaykovsky@gmail.com", actor)
        impersonation_headers = {**actor_headers, "X-Act-As-User": target}

        users_response = client.get("/admin/users/recent?limit=50", headers=impersonation_headers)
        assert users_response.status_code == 200
        users = users_response.get_json()["users"]
        target_user = next(user for user in users if user["id"] == target)
        assert target_user["email"] == target_email

        threads_response = client.get(
            f"/threads?act_as_user_id={target}",
            headers=actor_headers,
        )
        assert threads_response.status_code == 200
        assert any(row["thread_id"] == thread_id for row in threads_response.get_json()["threads"])

        effective_admin_response = client.get("/threads/recent", headers=impersonation_headers)
        assert effective_admin_response.status_code == 403

        write_response = client.post(
            "/strategy",
            headers=impersonation_headers,
            json={"thread_id": thread_id, "message": "impersonated write"},
        )
        assert write_response.status_code == 200
        payload = write_response.get_json()
        assert payload["thread_id"] == thread_id
        assert "python_code" not in payload
        assert "codex_thread_id" not in payload

        session = SessionLocal()
        try:
            written = session.get(Strategy, payload["id"])
            assert written is not None
            assert written.created_by == target
            assert written.created_by_email == target_email
        finally:
            session.close()
    finally:
        session = SessionLocal()
        try:
            session.query(Strategy).filter(Strategy.thread_id == thread_id).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()
        shutil.rmtree(workspace, ignore_errors=True)
        if previous_secret is None:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
        else:
            os.environ["SUPABASE_JWT_SECRET"] = previous_secret
