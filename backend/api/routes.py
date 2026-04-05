from __future__ import annotations

import json
import threading
import time

from flask import Blueprint, Response, current_app, g, jsonify, request
import logging
from pathlib import Path
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from auth import require_auth
from db.models import Strategy
from db.session import SessionLocal
from services.agent import (
    STRATEGIES_DIR,
    build_agent_reply,
    canvas_with_output,
    read_strategy_code,
    redact_secret_json_values_for_user,
    restore_strategy_workspace_from_snapshot,
    thread_id_allowed,
)
from langsmith import traceable

strategy_blueprint = Blueprint("strategy", __name__)
logger = logging.getLogger(__name__)

def _strategy_name_from_canvas(canvas: dict | None) -> str:
    if not isinstance(canvas, dict):
        return ""
    output = canvas.get("output")
    if not isinstance(output, dict):
        return ""
    data = output.get("data.json")
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
        "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
    }


def _latest_strategy(session: Session, thread_id: str, created_by: str | None = None) -> Strategy | None:
    query = (
        session.query(Strategy)
        .filter_by(thread_id=thread_id)
        .order_by(desc(Strategy.created_at))
    )
    if created_by:
        query = query.filter(Strategy.created_by == created_by)
    return query.first()


@strategy_blueprint.get("/threads")
@require_auth
def list_threads() -> tuple:
    uid = g.user_id
    session = SessionLocal()
    try:
        latest_per_thread = (
            session.query(
                Strategy.thread_id.label("thread_id"),
                func.max(Strategy.created_at).label("latest_created_at"),
            )
            .filter(Strategy.created_by == uid)
            .group_by(Strategy.thread_id)
            .subquery()
        )
        rows = (
            session.query(Strategy)
            .join(
                latest_per_thread,
                (Strategy.thread_id == latest_per_thread.c.thread_id)
                & (Strategy.created_at == latest_per_thread.c.latest_created_at),
            )
            .filter(Strategy.created_by == uid)
            .order_by(desc(Strategy.created_at))
            .all()
        )
        return (
            jsonify(
                {
                    "threads": [
                        {
                            "thread_id": row.thread_id,
                            "latest_run_id": row.id,
                            "latest_created_at": row.created_at.isoformat()
                            if row.created_at
                            else None,
                            "message_count": len(row.messages or []),
                            "strategy_name": _strategy_name_from_canvas(row.canvas),
                            "status": row.status,
                            "status_text": row.status_text or "",
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
            .filter_by(thread_id=thread_id, created_by=g.user_id)
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
        if target is None or target.thread_id != thread_id or target.created_by != g.user_id:
            return _validation_error("strategy not found")
        if target.created_at is None:
            return _validation_error("strategy has no created_at")

        deleted = (
            session.query(Strategy)
            .filter(Strategy.thread_id == thread_id, Strategy.created_by == g.user_id, Strategy.created_at > target.created_at)
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


def _run_strategy_agent_job(app_obj, run_id: str, thread_id: str, model: str) -> None:
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

    with app_obj.app_context():
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
                    extra={"thread_id": thread_id, "run_id": run_id, "model": model},
                )
                agent_result = build_agent_reply(
                    model=model,
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
                strategy.status = "success"
                strategy.status_text = ""
            except Exception as exc:
                logger.exception(
                    "agent job failed",
                    extra={"thread_id": thread_id, "run_id": run_id, "model": model},
                )
                strategy.status = "failure"
                strategy.status_text = str(exc)[:512]
                strategy.code = read_strategy_code(thread_id)
            session.add(strategy)
            session.commit()
        finally:
            session.close()


@traceable(name="get_or_create_strategy")
def get_or_create_strategy(session: Session, thread_id: str, created_by: str) -> Strategy:
    latest = _latest_strategy(session, thread_id, created_by=created_by)
    if latest is None:
        latest = Strategy(thread_id=thread_id, created_by=created_by, messages=[], canvas={})
        session.add(latest)
        session.flush()
    return latest


@strategy_blueprint.get("/strategy")
@require_auth
@traceable(name="get_strategy")
def get_strategy() -> tuple:
    uid = g.user_id
    run_id = request.args.get("id", "").strip()
    if run_id:
        session = SessionLocal()
        try:
            strategy = session.get(Strategy, run_id)
            if strategy is None or strategy.created_by != uid:
                return _validation_error("strategy not found")
            return jsonify(serialize_strategy(strategy)), 200
        finally:
            session.close()

    thread_id = request.args.get("thread_id", "").strip()
    if not thread_id:
        return _validation_error("thread_id or id query parameter is required")
    if not thread_id_allowed(thread_id):
        return _validation_error("invalid thread_id")

    session = SessionLocal()
    try:
        workspace = Path(STRATEGIES_DIR) / thread_id
        needs_restore = not workspace.is_dir()
        strategy = get_or_create_strategy(session, thread_id, created_by=uid)
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
@traceable(name="post_strategy")
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
        latest = _latest_strategy(session, thread_id, created_by=uid)
        if latest is not None and latest.status == "running":
            session.commit()
            out = serialize_strategy(latest)
            out["error"] = "A strategy update is already in progress."
            return jsonify(out), 409

        prev_messages = list(latest.messages or []) if latest else []
        prev_canvas = dict(latest.canvas or {}) if latest else {}
        prev_code = getattr(latest, "code", "") if latest else ""
        messages = prev_messages + [{"role": "user", "content": content}]

        new_strategy = Strategy(
            thread_id=thread_id,
            created_by=uid,
            messages=messages,
            canvas=prev_canvas,
            code=prev_code or read_strategy_code(thread_id),
            status="running",
            status_text="Starting…",
        )
        session.add(new_strategy)
        session.commit()
        session.refresh(new_strategy)

        run_id = new_strategy.id
        app_obj = current_app._get_current_object()
        model = app_obj.config["OPENROUTER_MODEL"]
        threading.Thread(
            target=_run_strategy_agent_job,
            args=(app_obj, run_id, thread_id, model),
            daemon=True,
        ).start()

        return jsonify(serialize_strategy(new_strategy)), 200
    finally:
        session.close()


@strategy_blueprint.get("/strategy/stream")
@require_auth
def strategy_stream():
    thread_id = request.args.get("thread_id", "").strip()
    if not thread_id or not thread_id_allowed(thread_id):
        return _validation_error("invalid or missing thread_id")

    uid = g.user_id

    def generate():
        last_snapshot = None
        last_keepalive = time.monotonic()
        while True:
            session = SessionLocal()
            try:
                strategy = _latest_strategy(session, thread_id, created_by=uid)
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
