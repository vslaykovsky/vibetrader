from __future__ import annotations

import importlib.util
import os
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


def test_strategy_thread_response_enriches_all_agent_messages_for_admin():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    thread_id = str(uuid.uuid4())
    created_at = datetime(2097, 1, 1, 12, 0, 0)
    session = SessionLocal()
    try:
        first = Strategy(
            thread_id=thread_id,
            messages=[],
            canvas={},
            code="",
            status="success",
            status_text="",
            langsmith_trace="https://smith.example/runs/first",
            created_at=created_at,
        )
        session.add(first)
        session.flush()
        first_messages = [
            {"role": "user", "content": "first prompt"},
            {"role": "assistant", "content": "first reply", "run_id": first.id},
        ]
        first.messages = first_messages

        second = Strategy(
            thread_id=thread_id,
            messages=[],
            canvas={},
            code="",
            status="success",
            status_text="",
            langsmith_trace="https://smith.example/runs/second",
            created_at=created_at + timedelta(minutes=1),
        )
        session.add(second)
        session.flush()
        second.messages = first_messages + [
            {"role": "user", "content": "second prompt"},
            {"role": "assistant", "content": "second reply", "run_id": second.id},
        ]
        session.commit()
        first_id = first.id
        second_id = second.id
    finally:
        session.close()

    app = create_app()
    try:
        response = app.test_client().get(
            f"/strategy?thread_id={thread_id}",
            headers=_auth_headers("vslaykovsky@gmail.com"),
        )
        assert response.status_code == 200
        assert response.get_json().get("messages") == [
            {"role": "user", "content": "first prompt"},
            {
                "role": "assistant",
                "content": "first reply",
                "run_id": first_id,
                "langsmith_trace": "https://smith.example/runs/first",
            },
            {"role": "user", "content": "second prompt"},
            {
                "role": "assistant",
                "content": "second reply",
                "run_id": second_id,
                "langsmith_trace": "https://smith.example/runs/second",
            },
        ]
    finally:
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


def test_strategy_lightweight_response_splits_canvas_payload_for_admin():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    thread_id = str(uuid.uuid4())
    code = "def run_strategy():\n    return {'ok': True}\n"
    canvas = {"output": {"backtest.json": {"strategy_name": "Split Load"}}}
    session = SessionLocal()
    try:
        strategy = Strategy(
            thread_id=thread_id,
            messages=[{"role": "user", "content": "hello"}],
            canvas=canvas,
            code=code,
            status="success",
            status_text="",
            strategy_name="Split Load",
        )
        session.add(strategy)
        session.commit()
        run_id = strategy.id
    finally:
        session.close()

    app = create_app()
    try:
        client = app.test_client()
        headers = _auth_headers("vslaykovsky@gmail.com")

        lightweight_res = client.get(
            f"/strategy?thread_id={thread_id}&include_canvas=0",
            headers=headers,
        )
        assert lightweight_res.status_code == 200
        lightweight_body = lightweight_res.get_json()
        assert lightweight_body == {
            "id": run_id,
            "thread_id": thread_id,
            "messages": [{"role": "user", "content": "hello"}],
            "status": "success",
            "status_text": "",
            "langsmith_trace": "",
            "strategy_name": "Split Load",
            "language": "",
            "created_at": lightweight_body["created_at"],
        }

        canvas_res = client.get(
            f"/strategy/canvas?thread_id={thread_id}",
            headers=headers,
        )
        assert canvas_res.status_code == 200
        canvas_body = canvas_res.get_json()
        assert canvas_body == {
            "id": run_id,
            "thread_id": thread_id,
            "canvas": canvas,
            "status": "success",
            "status_text": "",
            "strategy_name": "Split Load",
            "algorithm": "",
            "created_at": canvas_body["created_at"],
            "python_code": code,
            "codex_thread_id": "",
        }
    finally:
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)
