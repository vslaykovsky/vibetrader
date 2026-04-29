from __future__ import annotations

import json
import threading
import time

from flask import Blueprint, Response, current_app, g, jsonify, request
import logging
from pathlib import Path
from sqlalchemy import desc, text
from auth import require_auth
from db.models import Strategy
from db.session import SessionLocal
from db.strategy_queries import (
    ensure_latest_thread_strategy,
    get_strategy_by_id,
    latest_thread_strategy,
)
from services.agent import (
    STRATEGIES_DIR,
    CHAT_MODEL,
    build_agent_reply,
    canvas_with_output,
    generate_strategy_algorithm_pseudocode,
    read_strategy_code,
    redact_secret_json_values_for_user,
    restore_strategy_workspace_from_snapshot,
    thread_id_allowed,
)
from services.conversation_language import detect_conversation_language_iso
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

strategy_blueprint = Blueprint("strategy", __name__)
logger = logging.getLogger(__name__)

def _strategy_name_from_canvas(canvas: dict | None) -> str:
    if not isinstance(canvas, dict):
        return ""
    output = canvas.get("output")
    if not isinstance(output, dict):
        return ""
    data = output.get("backtest.json")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return ""
    if not isinstance(data, dict):
        return ""
    name = data.get("strategy_name")
    return name.strip() if isinstance(name, str) else ""


def serialize_strategy(strategy: Strategy) -> dict:
    canvas = redact_secret_json_values_for_user(dict(strategy.canvas or {}))
    messages = redact_secret_json_values_for_user(list(strategy.messages or []))
    return {
        "id": strategy.id,
        "thread_id": strategy.thread_id,
        "messages": messages,
        "canvas": canvas,
        "status": strategy.status,
        "status_text": strategy.status_text or "",
        "langsmith_trace": strategy.langsmith_trace or "",
        "strategy_name": strategy.strategy_name or "",
        "algorithm": strategy.algorithm or "",
        "language": strategy.language or "",
        "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
    }


@strategy_blueprint.get("/threads")
@require_auth
def list_threads() -> tuple:
    session = SessionLocal()
    try:
        sql = """
SELECT id, thread_id, created_at, messages_count, status, status_text, strategy_name
FROM (
    SELECT DISTINCT ON (thread_id)
        id, thread_id, created_at, messages_count, status, status_text, strategy_name
    FROM strategy
    ORDER BY thread_id, created_at DESC, id DESC
) latest
ORDER BY created_at DESC, id DESC
"""
        stmt = text(sql)
        logger.info("list_threads SQL: %s", sql.strip())
        rows = session.execute(stmt).mappings().all()
        return (
            jsonify(
                {
                    "threads": [
                        {
                            "thread_id": row["thread_id"],
                            "latest_run_id": row["id"],
                            "latest_created_at": row["created_at"].isoformat()
                            if row["created_at"]
                            else None,
                            "message_count": int(row["messages_count"] or 0),
                            "strategy_name": (row["strategy_name"] or "").strip()
                            or "unknown strategy",
                            "status": row["status"],
                            "status_text": row["status_text"] or "",
                        }
                        for row in rows
                    ]
                }
            ),
            200,
        )
    finally:
        session.close()


@strategy_blueprint.delete("/threads/<thread_id>")
@require_auth
def delete_thread(thread_id: str) -> tuple:
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return _validation_error("thread_id is required")
    if not thread_id_allowed(thread_id):
        return _validation_error("invalid thread_id")

    session = SessionLocal()
    try:
        deleted = (
            session.query(Strategy)
            .filter_by(thread_id=thread_id)
            .delete(synchronize_session=False)
        )
        session.commit()
        return jsonify({"ok": True, "thread_id": thread_id, "deleted_runs": deleted}), 200
    finally:
        session.close()


@strategy_blueprint.post("/threads/<thread_id>/revert")
@require_auth
def revert_thread(thread_id: str) -> tuple:
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return _validation_error("thread_id is required")
    if not thread_id_allowed(thread_id):
        return _validation_error("invalid thread_id")

    payload = request.get_json(silent=True) or {}
    run_id = str(payload.get("run_id", "")).strip()
    if not run_id:
        return _validation_error("run_id is required")

    session = SessionLocal()
    try:
        target = session.get(Strategy, run_id)
        if target is None or target.thread_id != thread_id:
            return _validation_error("strategy not found")
        if target.created_at is None:
            return _validation_error("strategy has no created_at")

        deleted = (
            session.query(Strategy)
            .filter(Strategy.thread_id == thread_id, Strategy.created_at > target.created_at)
            .delete(synchronize_session=False)
        )
        session.commit()

        restore_strategy_workspace_from_snapshot(
            thread_id,
            code=getattr(target, "code", "") or "",
            canvas=dict(getattr(target, "canvas", {}) or {}),
        )
        return (
            jsonify(
                {
                    "ok": True,
                    "thread_id": thread_id,
                    "reverted_to_run_id": run_id,
                    "deleted_runs": deleted,
                }
            ),
            200,
        )
    finally:
        session.close()


def _validation_error(message: str) -> tuple:
    return (
        jsonify(
            {
                "error": message,
                "status": None,
                "status_text": None,
            }
        ),
        400,
    )


def _apply_langsmith_trace(strategy: Strategy) -> None:
    rt = get_current_run_tree()
    if rt is None:
        return
    try:
        strategy.langsmith_trace = rt.get_url()
    except Exception:
        tid = getattr(rt, "trace_id", None) or getattr(rt, "id", None)
        strategy.langsmith_trace = str(tid) if tid is not None else ""


def _stamp_langsmith_thread_metadata(thread_id: str) -> None:
    tid = (thread_id or "").strip()
    if not tid:
        return
    rt = get_current_run_tree()
    if rt is not None:
        rt.metadata["thread_id"] = tid


@traceable(name="post_strategy")
def _execute_strategy_agent_job(run_id: str, thread_id: str) -> None:
    def persist_status_text(text: str) -> None:
        t = (text or "")[:512]
        s = SessionLocal()
        try:
            row = s.get(Strategy, run_id)
            if row is not None:
                row.status_text = t
                s.add(row)
                s.commit()
        finally:
            s.close()

    session = SessionLocal()
    try:
        strategy = session.get(Strategy, run_id)
        if strategy is None:
            return
        messages = list(strategy.messages or [])
        canvas = dict(strategy.canvas or {})
        try:
            logger.info(
                "agent job started",
                extra={"thread_id": thread_id, "run_id": run_id, "model": CHAT_MODEL},
            )
            agent_result = build_agent_reply(
                messages=messages,
                existing_canvas=canvas,
                thread_id=thread_id,
                on_progress=persist_status_text,
            )
            assistant_entry: dict = {
                "role": "assistant",
                "content": agent_result["message"],
                "run_id": run_id,
            }
            rd = agent_result.get("reply_duration_ms")
            if isinstance(rd, int) and rd >= 0:
                assistant_entry["reply_duration_ms"] = rd
            messages.append(assistant_entry)
            strategy.messages = messages
            strategy.canvas = canvas_with_output(dict(agent_result["canvas"] or {}), thread_id)
            strategy.code = read_strategy_code(thread_id)
            sn = (
                str(agent_result.get("strategy_name") or "").strip()
                or _strategy_name_from_canvas(strategy.canvas)
            )
            if sn:
                strategy.strategy_name = sn[:512]
            strategy.status = "success"
            strategy.status_text = ""
        except Exception as exc:
            logger.exception(
                "agent job failed",
                extra={"thread_id": thread_id, "run_id": run_id, "model": CHAT_MODEL},
            )
            strategy.status = "failure"
            strategy.status_text = str(exc)[:512]
            strategy.code = read_strategy_code(thread_id)
        _apply_langsmith_trace(strategy)
        session.add(strategy)
        session.commit()
    finally:
        session.close()


def _run_strategy_agent_job(app_obj, run_id: str, thread_id: str) -> None:
    with app_obj.app_context():
        _execute_strategy_agent_job(
            run_id,
            thread_id,
            langsmith_extra={"metadata": {"thread_id": thread_id}},
        )


@strategy_blueprint.patch("/strategy")
@require_auth
def patch_strategy() -> tuple:
    uid = g.user_id
    payload = request.get_json(silent=True) or {}
    run_id = str(payload.get("id", "")).strip()
    if not run_id:
        return _validation_error("id is required")
    raw = payload.get("strategy_name", "")
    if not isinstance(raw, str):
        return _validation_error("strategy_name must be a string")
    name = str(raw)[:512]

    session = SessionLocal()
    try:
        strategy = get_strategy_by_id(session, run_id)
        if strategy is None:
            return _validation_error("strategy not found")
        owner = (strategy.created_by or "").strip()
        if owner and owner != str(uid or "").strip():
            return jsonify({"error": "forbidden", "status": None, "status_text": None}), 403
        strategy.strategy_name = name.strip()
        session.add(strategy)
        session.commit()
        return jsonify(serialize_strategy(strategy)), 200
    finally:
        session.close()


@strategy_blueprint.get("/strategy")
@require_auth
@traceable(name="get_strategy")
def get_strategy() -> tuple:
    uid = g.user_id
    run_id = request.args.get("id", "").strip()
    if run_id:
        session = SessionLocal()
        try:
            strategy = get_strategy_by_id(session, run_id)
            if strategy is None:
                return _validation_error("strategy not found")
            _stamp_langsmith_thread_metadata(strategy.thread_id)
            return jsonify(serialize_strategy(strategy)), 200
        finally:
            session.close()

    thread_id = request.args.get("thread_id", "").strip()
    if not thread_id:
        return _validation_error("thread_id or id query parameter is required")
    if not thread_id_allowed(thread_id):
        return _validation_error("invalid thread_id")
    _stamp_langsmith_thread_metadata(thread_id)

    session = SessionLocal()
    try:
        workspace = Path(STRATEGIES_DIR) / thread_id
        needs_restore = not workspace.is_dir()
        strategy = ensure_latest_thread_strategy(
            session,
            thread_id,
            uid,
            getattr(g, "user_email", None),
        )
        session.commit()
        if needs_restore:
            restore_strategy_workspace_from_snapshot(
                thread_id,
                code=getattr(strategy, "code", "") or "",
                canvas=dict(getattr(strategy, "canvas", {}) or {}),
            )
        return jsonify(serialize_strategy(strategy)), 200
    finally:
        session.close()


@strategy_blueprint.post("/strategy")
@require_auth
def post_strategy() -> tuple:
    uid = g.user_id
    payload = request.get_json(silent=True) or {}
    thread_id = str(payload.get("thread_id", "")).strip()
    content = str(payload.get("message", "")).strip()

    if not thread_id:
        return _validation_error("thread_id is required")
    if not thread_id_allowed(thread_id):
        return _validation_error("invalid thread_id")
    if not content:
        return _validation_error("message is required")

    session = SessionLocal()
    try:
        running = (
            session.query(Strategy)
            .filter(Strategy.thread_id == thread_id, Strategy.status == "running")
            .order_by(desc(Strategy.created_at))
            .first()
        )
        if running is not None:
            session.commit()
            out = serialize_strategy(running)
            out["error"] = "A strategy update is already in progress."
            return jsonify(out), 409

        latest = latest_thread_strategy(session, thread_id)
        prev_messages = list(latest.messages or []) if latest else []
        prev_canvas = dict(latest.canvas or {}) if latest else {}
        prev_code = getattr(latest, "code", "") if latest else ""
        messages = prev_messages + [{"role": "user", "content": content}]
        lang = detect_conversation_language_iso(messages)

        new_strategy = Strategy(
            thread_id=thread_id,
            created_by=uid,
            created_by_email=getattr(g, "user_email", None),
            messages=messages,
            canvas=prev_canvas,
            code=prev_code or read_strategy_code(thread_id),
            status="running",
            status_text="Starting…",
            language=lang,
        )
        session.add(new_strategy)
        session.commit()
        session.refresh(new_strategy)

        run_id = new_strategy.id
        app_obj = current_app._get_current_object()
        threading.Thread(
            target=_run_strategy_agent_job,
            args=(app_obj, run_id, thread_id),
            daemon=True,
        ).start()

        return jsonify(serialize_strategy(new_strategy)), 200
    finally:
        session.close()


@strategy_blueprint.post("/strategy/algorithm")
@require_auth
@traceable
def post_strategy_algorithm() -> tuple:
    payload = request.get_json(silent=True) or {}
    run_id = str(payload.get("id", "")).strip()
    if not run_id:
        return _validation_error("id is required")

    session = SessionLocal()
    try:
        strategy = get_strategy_by_id(session, run_id)
        if strategy is None:
            return _validation_error("strategy not found")
        existing = (getattr(strategy, "algorithm", None) or "").strip()
        if existing:
            return jsonify({"id": strategy.id, "algorithm": existing}), 200

        gen = generate_strategy_algorithm_pseudocode(
            code=str(strategy.code or ""),
            language=str(getattr(strategy, "language", None) or ""),
        )
        if not gen.get("ok"):
            return (
                jsonify(
                    {
                        "error": gen.get("error") or "algorithm generation failed",
                        "id": strategy.id,
                        "algorithm": "",
                    }
                ),
                502,
            )
        text = str(gen.get("algorithm") or "").strip()
        if len(text) > 120_000:
            text = text[:120_000]
        strategy.algorithm = text
        session.add(strategy)
        session.commit()
        return jsonify({"id": strategy.id, "algorithm": strategy.algorithm}), 200
    finally:
        session.close()


@strategy_blueprint.get("/strategy/stream")
@require_auth
def strategy_stream():
    thread_id = request.args.get("thread_id", "").strip()
    if not thread_id or not thread_id_allowed(thread_id):
        return _validation_error("invalid or missing thread_id")

    def generate():
        last_snapshot = None
        last_keepalive = time.monotonic()
        while True:
            session = SessionLocal()
            try:
                strategy = latest_thread_strategy(session, thread_id)
                if strategy is None:
                    break
                snapshot = json.dumps(serialize_strategy(strategy), sort_keys=True)
                if snapshot != last_snapshot:
                    last_snapshot = snapshot
                    yield f"data: {snapshot}\n\n"
                    last_keepalive = time.monotonic()
                done = strategy.status != "running"
            finally:
                session.close()
            if done:
                break
            if (time.monotonic() - last_keepalive) >= 15:
                yield ": keepalive\n\n"
                last_keepalive = time.monotonic()
            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
