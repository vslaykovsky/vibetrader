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
_spec = importlib.util.spec_from_file_location("vibetrader_flask_app", _ROOT / "app.py")
assert _spec and _spec.loader
_flask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_flask)
create_app = _flask.create_app


def _auth_headers(email: str, sub: str) -> dict[str, str]:
    secret = os.environ["SUPABASE_JWT_SECRET"]
    tok = jwt.encode(
        {
            "sub": sub,
            "email": email,
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {tok}"}


def test_list_threads_returns_only_authenticated_user_threads():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    owner = f"thread-owner-{uuid.uuid4()}"
    other = f"thread-other-{uuid.uuid4()}"
    thread_id = str(uuid.uuid4())
    other_thread_id = str(uuid.uuid4())
    created_at = datetime(2098, 1, 1, 12, 0, 0)
    session = SessionLocal()
    try:
        row = Strategy(
            thread_id=thread_id,
            created_by=owner,
            created_by_email="owner@example.com",
            messages=[{"role": "user", "content": "hello"}],
            canvas={},
            code="",
            status="success",
            status_text="",
            strategy_name="Owner strategy",
            created_at=created_at,
        )
        session.add(row)
        session.add(
            Strategy(
                thread_id=other_thread_id,
                created_by=other,
                created_by_email="other@example.com",
                messages=[{"role": "user", "content": "hidden"}],
                canvas={},
                code="",
                status="success",
                status_text="",
                strategy_name="Other strategy",
                created_at=created_at + timedelta(minutes=1),
            )
        )
        session.commit()
        run_id = row.id
    finally:
        session.close()

    app = create_app()
    try:
        response = app.test_client().get(
            "/threads",
            headers=_auth_headers("owner@example.com", owner),
        )
        assert response.status_code == 200
        assert response.get_json() == {
            "threads": [
                {
                    "thread_id": thread_id,
                    "latest_run_id": run_id,
                    "latest_created_at": created_at.isoformat(),
                    "message_count": 1,
                    "strategy_name": "Owner strategy",
                    "status": "success",
                    "status_text": "",
                }
            ]
        }
    finally:
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


def test_strategy_thread_routes_use_authenticated_user_latest_run():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    owner = f"thread-owner-{uuid.uuid4()}"
    other = f"thread-other-{uuid.uuid4()}"
    thread_id = str(uuid.uuid4())
    owner_created_at = datetime(2096, 1, 1, 12, 0, 0)
    other_created_at = owner_created_at + timedelta(minutes=1)
    session = SessionLocal()
    try:
        owner_row = Strategy(
            thread_id=thread_id,
            created_by=owner,
            created_by_email="owner@example.com",
            messages=[{"role": "user", "content": "owner prompt"}],
            canvas={"output": {"data.json": {"owner": True}}},
            code="",
            status="success",
            status_text="",
            strategy_name="Owner strategy",
            created_at=owner_created_at,
        )
        other_row = Strategy(
            thread_id=thread_id,
            created_by=other,
            created_by_email="other@example.com",
            messages=[{"role": "user", "content": "other prompt"}],
            canvas={"output": {"data.json": {"other": True}}},
            code="",
            status="running",
            status_text="Thinking…",
            strategy_name="Other strategy",
            created_at=other_created_at,
        )
        session.add(owner_row)
        session.add(other_row)
        session.commit()
        owner_run_id = owner_row.id
        other_run_id = other_row.id
    finally:
        session.close()

    app = create_app()
    try:
        client = app.test_client()
        owner_headers = _auth_headers("owner@example.com", owner)
        other_headers = _auth_headers("other@example.com", other)

        owner_response = client.get(
            f"/strategy?thread_id={thread_id}&include_canvas=0",
            headers=owner_headers,
        )
        assert owner_response.status_code == 200
        assert owner_response.get_json() == {
            "id": owner_run_id,
            "thread_id": thread_id,
            "messages": [{"role": "user", "content": "owner prompt"}],
            "status": "success",
            "status_text": "",
            "langsmith_trace": "",
            "strategy_name": "Owner strategy",
            "language": "",
            "created_at": owner_created_at.isoformat(),
        }

        owner_canvas_response = client.get(
            f"/strategy/canvas?thread_id={thread_id}",
            headers=owner_headers,
        )
        assert owner_canvas_response.status_code == 200
        assert owner_canvas_response.get_json() == {
            "id": owner_run_id,
            "thread_id": thread_id,
            "canvas": {"output": {"data.json": {"owner": True}}},
            "status": "success",
            "status_text": "",
            "strategy_name": "Owner strategy",
            "algorithm": "",
            "created_at": owner_created_at.isoformat(),
        }

        other_response = client.get(
            f"/strategy?thread_id={thread_id}&include_canvas=0",
            headers=other_headers,
        )
        assert other_response.status_code == 200
        assert other_response.get_json() == {
            "id": other_run_id,
            "thread_id": thread_id,
            "messages": [{"role": "user", "content": "other prompt"}],
            "status": "running",
            "status_text": "Thinking…",
            "langsmith_trace": "",
            "strategy_name": "Other strategy",
            "language": "",
            "created_at": other_created_at.isoformat(),
        }
    finally:
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


def test_strategy_stream_returns_no_content_for_finished_latest_run():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    owner = f"stream-owner-{uuid.uuid4()}"
    thread_id = str(uuid.uuid4())
    session = SessionLocal()
    try:
        row = Strategy(
            thread_id=thread_id,
            created_by=owner,
            created_by_email="owner@example.com",
            messages=[{"role": "user", "content": "done"}],
            canvas={"output": {"data.json": {"done": True}}},
            code="",
            status="success",
            status_text="",
        )
        session.add(row)
        session.commit()
    finally:
        session.close()

    app = create_app()
    try:
        response = app.test_client().get(
            f"/strategy/stream?thread_id={thread_id}",
            headers=_auth_headers("owner@example.com", owner),
        )
        assert response.status_code == 204
        assert response.get_data() == b""
    finally:
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


def test_revert_thread_deletes_later_running_run():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    owner = f"revert-owner-{uuid.uuid4()}"
    thread_id = str(uuid.uuid4())
    created_at = datetime(2097, 1, 1, 12, 0, 0)
    first_code = "def run_strategy():\n    return {'first': True}\n"
    workspace = _ROOT / "strategies_v2" / thread_id
    session = SessionLocal()
    try:
        first = Strategy(
            thread_id=thread_id,
            created_by=owner,
            created_by_email="owner@example.com",
            messages=[
                {"role": "user", "content": "first prompt"},
            ],
            canvas={},
            code=first_code,
            status="success",
            status_text="",
            created_at=created_at,
        )
        session.add(first)
        session.flush()
        running = Strategy(
            thread_id=thread_id,
            created_by=owner,
            created_by_email="owner@example.com",
            messages=[
                {"role": "user", "content": "first prompt"},
                {"role": "assistant", "content": "first reply", "run_id": first.id},
                {"role": "user", "content": "running prompt"},
            ],
            canvas={},
            code="def run_strategy():\n    return {'running': True}\n",
            status="running",
            status_text="Working...",
            created_at=created_at + timedelta(minutes=1),
        )
        session.add(running)
        session.commit()
        first_id = first.id
        running_id = running.id
    finally:
        session.close()

    app = create_app()
    try:
        response = app.test_client().post(
            f"/threads/{thread_id}/revert",
            headers=_auth_headers("owner@example.com", owner),
            json={"run_id": first_id},
        )
        assert response.status_code == 200
        assert response.get_json() == {
            "ok": True,
            "thread_id": thread_id,
            "reverted_to_run_id": first_id,
            "deleted_runs": 1,
        }

        session = SessionLocal()
        try:
            assert session.get(Strategy, first_id) is not None
            assert session.get(Strategy, running_id) is None
        finally:
            session.close()
        assert (workspace / "strategy.py").read_text(encoding="utf-8") == first_code
    finally:
        session = SessionLocal()
        try:
            session.query(Strategy).filter_by(thread_id=thread_id).delete(
                synchronize_session=False
            )
            session.commit()
        finally:
            session.close()
        shutil.rmtree(workspace, ignore_errors=True)
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


def test_list_recent_threads_is_admin_only_and_returns_latest_ten_threads():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    base = datetime(2099, 1, 1, 12, 0, 0)
    rows = []
    admin_sub = f"admin-recent-user-{uuid.uuid4()}"
    session = SessionLocal()
    try:
        for i in range(12):
            row = Strategy(
                thread_id=str(uuid.uuid4()),
                created_by=f"recent-user-{i}",
                created_by_email=f"recent-user-{i}@example.com",
                messages=[{"role": "user", "content": f"message {i}"}],
                canvas={},
                code="",
                status="success",
                status_text="",
                strategy_name=f"Recent strategy {i}",
                created_at=base + timedelta(minutes=i),
            )
            session.add(row)
            rows.append(row)
        session.add(
            Strategy(
                thread_id=str(uuid.uuid4()),
                created_by=admin_sub,
                created_by_email="vslaykovsky@gmail.com",
                messages=[{"role": "user", "content": "own admin message"}],
                canvas={},
                code="",
                status="success",
                status_text="",
                strategy_name="Admin own strategy",
                created_at=base + timedelta(minutes=99),
            )
        )
        session.commit()
        expected_rows = [
            {
                "thread_id": row.thread_id,
                "latest_run_id": row.id,
                "latest_created_at": row.created_at.isoformat(),
                "message_count": 1,
                "strategy_name": row.strategy_name,
                "status": row.status,
                "status_text": row.status_text,
                "created_by": row.created_by,
                "created_by_email": row.created_by_email,
            }
            for row in reversed(rows[2:])
        ]
    finally:
        session.close()

    app = create_app()
    try:
        client = app.test_client()
        forbidden = client.get(
            "/threads/recent",
            headers=_auth_headers("user@example.com", "non-admin-recent-user"),
        )
        assert forbidden.status_code == 403
        assert forbidden.get_json() == {"error": "forbidden"}

        response = client.get(
            "/threads/recent",
            headers=_auth_headers("vslaykovsky@gmail.com", admin_sub),
        )
        assert response.status_code == 200
        assert response.get_json() == {"threads": expected_rows}
    finally:
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
