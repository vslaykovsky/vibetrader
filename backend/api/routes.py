from __future__ import annotations

import json
import threading
import time

from flask import Blueprint, Response, current_app, jsonify, request
import logging
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from db.models import Strategy
from db.session import SessionLocal
from services.agent import (
    build_agent_reply,
    canvas_with_output,
    read_strategy_code,
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
    canvas = dict(strategy.canvas or {})
    return {
        "id": strategy.id,
        "thread_id": strategy.thread_id,
        "messages": strategy.messages or [],
        "canvas": canvas,
        "status": strategy.status,
        "status_text": strategy.status_text or "",
        "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
    }


def _latest_strategy(session: Session, thread_id: str) -> Strategy | None:
    # Build the query
    query = (
        session.query(Strategy)
        .filter_by(thread_id=thread_id)
        .order_by(desc(Strategy.created_at))
    )
    # Log the SQL query for debugging/inspection
    return query.first()


@strategy_blueprint.get("/threads")
def list_threads() -> tuple:
    session = SessionLocal()
    try:
        latest_per_thread = (
            session.query(
                Strategy.thread_id.label("thread_id"),
                func.max(Strategy.created_at).label("latest_created_at"),
            )
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
                messages.append({
                    "role": "assistant",
                    "content": agent_result["message"],
                    "run_id": run_id,
                })
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
def get_or_create_strategy(session: Session, thread_id: str) -> Strategy:
    latest = _latest_strategy(session, thread_id)
    if latest is None:
        latest = Strategy(thread_id=thread_id, messages=[], canvas={})
        session.add(latest)
        session.flush()
    return latest


@strategy_blueprint.get("/strategy")
@traceable(name="get_strategy")
def get_strategy() -> tuple:
    run_id = request.args.get("id", "").strip()
    if run_id:
        session = SessionLocal()
        try:
            strategy = session.get(Strategy, run_id)
            if strategy is None:
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
        strategy = get_or_create_strategy(session, thread_id)
        session.commit()
        return jsonify(serialize_strategy(strategy)), 200
    finally:
        session.close()


@strategy_blueprint.post("/strategy")
@traceable(name="post_strategy")
def post_strategy() -> tuple:
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
        latest = _latest_strategy(session, thread_id)
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
                strategy = _latest_strategy(session, thread_id)
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
