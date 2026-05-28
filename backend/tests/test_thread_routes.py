from __future__ import annotations

import importlib.util
import json
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
from api.routes import _restore_thread_workspace_from_latest_snapshot


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


def test_branch_thread_copies_agent_reply_snapshot_to_new_thread():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    owner = f"branch-owner-{uuid.uuid4()}"
    source_thread_id = str(uuid.uuid4())
    created_at = datetime(2096, 6, 1, 12, 0, 0)
    code = "def run_strategy():\n    return {'branch': True}\n"
    canvas = {"output": {"data.json": {"branch": True}, "notes.md": "branch notes"}}
    session = SessionLocal()
    try:
        source = Strategy(
            thread_id=source_thread_id,
            created_by=owner,
            created_by_email="owner@example.com",
            messages=[],
            canvas=canvas,
            code=code,
            status="success",
            status_text="",
            strategy_name="Branch source",
            strategy_name_source="manual",
            algorithm="Branch algorithm",
            language="en",
            created_at=created_at,
        )
        session.add(source)
        session.flush()
        source.messages = [
            {"role": "user", "content": "build source"},
            {
                "role": "assistant",
                "content": "source reply",
                "run_id": source.id,
                "reply_duration_ms": 123,
            },
        ]
        session.commit()
        source_run_id = source.id
    finally:
        session.close()

    new_thread_id = ""
    app = create_app()
    try:
        response = app.test_client().post(
            f"/threads/{source_thread_id}/branch",
            headers=_auth_headers("owner@example.com", owner),
            json={"run_id": source_run_id},
        )
        assert response.status_code == 200
        payload = response.get_json()
        new_thread_id = payload["thread_id"]
        new_run_id = payload["id"]
        new_created_at = payload["created_at"]
        assert payload == {
            "id": new_run_id,
            "thread_id": new_thread_id,
            "messages": [
                {
                    "role": "assistant",
                    "content": "source reply",
                    "run_id": new_run_id,
                    "reply_duration_ms": 123,
                }
            ],
            "status": "success",
            "status_text": "",
            "langsmith_trace": "",
            "strategy_name": "Branch source",
            "language": "en",
            "created_at": new_created_at,
            "canvas": canvas,
            "algorithm": "Branch algorithm",
            "ok": True,
            "source_thread_id": source_thread_id,
            "source_run_id": source_run_id,
        }

        session = SessionLocal()
        try:
            branched = session.get(Strategy, new_run_id)
            assert branched is not None
            assert branched.created_by == owner
            assert branched.messages == [
                {
                    "role": "assistant",
                    "content": "source reply",
                    "run_id": new_run_id,
                    "reply_duration_ms": 123,
                }
            ]
            assert branched.messages_count == 1
            assert branched.canvas == canvas
            assert branched.code == code
            assert branched.codex_thread_id == ""
        finally:
            session.close()

        workspace = _ROOT / "strategies_v2" / new_thread_id
        assert (workspace / "strategy.py").read_text(encoding="utf-8") == code
    finally:
        session = SessionLocal()
        try:
            if new_thread_id:
                session.query(Strategy).filter_by(thread_id=new_thread_id).delete(
                    synchronize_session=False
                )
            session.query(Strategy).filter_by(thread_id=source_thread_id).delete(
                synchronize_session=False
            )
            session.commit()
        finally:
            session.close()
        if new_thread_id:
            shutil.rmtree(_ROOT / "strategies_v2" / new_thread_id, ignore_errors=True)
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


def test_strategy_thread_routes_return_latest_shared_thread_run():
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
            messages=[],
            canvas={"output": {"data.json": {"owner": True}}},
            code="",
            status="success",
            status_text="",
            strategy_name="Owner strategy",
            created_at=owner_created_at,
        )
        session.add(owner_row)
        session.flush()
        owner_row.messages = [
            {"role": "user", "content": "owner prompt"},
            {"role": "assistant", "content": "owner reply", "run_id": owner_row.id},
        ]
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
            "id": other_run_id,
            "thread_id": thread_id,
            "messages": [
                {"role": "user", "content": "owner prompt"},
                {"role": "assistant", "content": "owner reply", "run_id": owner_run_id},
                {"role": "user", "content": "other prompt"},
            ],
            "status": "running",
            "status_text": "Thinking…",
            "langsmith_trace": "",
            "strategy_name": "Other strategy",
            "language": "",
            "created_at": other_created_at.isoformat(),
        }

        owner_canvas_response = client.get(
            f"/strategy/canvas?thread_id={thread_id}",
            headers=owner_headers,
        )
        assert owner_canvas_response.status_code == 200
        assert owner_canvas_response.get_json() == {
            "id": other_run_id,
            "thread_id": thread_id,
            "canvas": {"output": {"data.json": {"other": True}}},
            "status": "running",
            "status_text": "Thinking…",
            "strategy_name": "Other strategy",
            "algorithm": "",
            "created_at": other_created_at.isoformat(),
        }

        other_response = client.get(
            f"/strategy?thread_id={thread_id}&include_canvas=0",
            headers=other_headers,
        )
        assert other_response.status_code == 200
        assert other_response.get_json() == {
            "id": other_run_id,
            "thread_id": thread_id,
            "messages": [
                {"role": "user", "content": "owner prompt"},
                {"role": "assistant", "content": "owner reply", "run_id": owner_run_id},
                {"role": "user", "content": "other prompt"},
            ],
            "status": "running",
            "status_text": "Thinking…",
            "langsmith_trace": "",
            "strategy_name": "Other strategy",
            "language": "",
            "created_at": other_created_at.isoformat(),
        }

        owner_run_response = client.get(
            f"/strategy?id={other_run_id}&include_canvas=0",
            headers=owner_headers,
        )
        assert owner_run_response.status_code == 200
        assert owner_run_response.get_json() == {
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

        owner_run_canvas_response = client.get(
            f"/strategy/canvas?id={other_run_id}",
            headers=owner_headers,
        )
        assert owner_run_canvas_response.status_code == 200
        assert owner_run_canvas_response.get_json() == {
            "id": other_run_id,
            "thread_id": thread_id,
            "canvas": {"output": {"data.json": {"other": True}}},
            "status": "running",
            "status_text": "Thinking…",
            "strategy_name": "Other strategy",
            "algorithm": "",
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


def test_post_strategy_blocks_on_other_users_running_thread_run():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    owner = f"post-owner-{uuid.uuid4()}"
    requester = f"post-requester-{uuid.uuid4()}"
    thread_id = str(uuid.uuid4())
    created_at = datetime(2097, 1, 1, 12, 0, 0)
    session = SessionLocal()
    try:
        running = Strategy(
            thread_id=thread_id,
            created_by=owner,
            created_by_email="owner@example.com",
            messages=[
                {"role": "user", "content": "owner prompt"},
                {"role": "assistant", "content": "working reply"},
            ],
            canvas={"output": {"data.json": {"running": True}}},
            code="",
            status="running",
            status_text="Working...",
            strategy_name="Owner running strategy",
            created_at=created_at,
        )
        session.add(running)
        session.commit()
        running_id = running.id
    finally:
        session.close()

    app = create_app()
    try:
        response = app.test_client().post(
            "/strategy",
            headers=_auth_headers("requester@example.com", requester),
            json={"thread_id": thread_id, "message": "requester prompt"},
        )
        assert response.status_code == 409
        assert response.get_json() == {
            "id": running_id,
            "thread_id": thread_id,
            "messages": [
                {"role": "user", "content": "owner prompt"},
                {"role": "assistant", "content": "working reply"},
            ],
            "status": "running",
            "status_text": "Working...",
            "langsmith_trace": "",
            "strategy_name": "Owner running strategy",
            "language": "",
            "created_at": created_at.isoformat(),
            "canvas": {"output": {"data.json": {"running": True}}},
            "algorithm": "",
            "error": "A strategy update is already in progress.",
        }
    finally:
        session = SessionLocal()
        try:
            session.query(Strategy).filter_by(thread_id=thread_id).delete(
                synchronize_session=False
            )
            session.commit()
        finally:
            session.close()
        if prev is not None:
            os.environ["SUPABASE_JWT_SECRET"] = prev
        else:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


def test_revert_thread_deletes_other_users_later_running_run():
    prev = os.environ.get("SUPABASE_JWT_SECRET")
    os.environ["SUPABASE_JWT_SECRET"] = "pytest-live-secret-32-chars-minimum!!"
    owner = f"revert-owner-{uuid.uuid4()}"
    requester = f"revert-requester-{uuid.uuid4()}"
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
            headers=_auth_headers("requester@example.com", requester),
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


def test_restore_thread_workspace_from_latest_snapshot_uses_newest_stored_artifacts():
    thread_id = str(uuid.uuid4())
    workspace = _ROOT / "strategies_v2" / thread_id
    created_at = datetime(2097, 2, 1, 12, 0, 0)
    old_code = "print('old strategy')\n"
    session = SessionLocal()
    try:
        old = Strategy(
            thread_id=thread_id,
            messages=[],
            canvas={
                "output": {
                    "params.json": {"ticker": "OLD", "start_date": "2021-01-01"},
                    "params-hyperopt.json": {"n_trials": 7},
                }
            },
            code=old_code,
            status="success",
            status_text="",
            created_at=created_at,
        )
        session.add(old)
        latest = Strategy(
            thread_id=thread_id,
            messages=[],
            canvas={"output": {"params.json": {"ticker": "NEW", "start_date": "2022-01-01"}}},
            code="",
            status="success",
            status_text="",
            created_at=created_at + timedelta(minutes=1),
        )
        session.add(latest)
        session.commit()
        session.refresh(latest)

        shutil.rmtree(workspace, ignore_errors=True)
        _restore_thread_workspace_from_latest_snapshot(session, thread_id, latest)

        assert (workspace / "strategy.py").read_text(encoding="utf-8") == old_code
        assert json.loads((workspace / "params.json").read_text(encoding="utf-8")) == {
            "start_date": "2022-01-01",
            "ticker": "NEW",
        }
        assert json.loads((workspace / "params-hyperopt.json").read_text(encoding="utf-8")) == {
            "n_trials": 7,
        }
    finally:
        session.query(Strategy).filter_by(thread_id=thread_id).delete(synchronize_session=False)
        session.commit()
        session.close()
        shutil.rmtree(workspace, ignore_errors=True)


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
