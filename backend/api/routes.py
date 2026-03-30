from __future__ import annotations

import threading

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.orm import Session

from db.models import Strategy
from db.session import SessionLocal
from services.agent import build_agent_reply, canvas_with_output, thread_id_allowed
from langsmith import traceable

strategy_blueprint = Blueprint("strategy", __name__)


def serialize_strategy(strategy: Strategy) -> dict:
    return {
        "thread_id": strategy.thread_id,
        "messages": strategy.messages or [],
        "canvas": canvas_with_output(dict(strategy.canvas or {}), strategy.thread_id),
        "status": strategy.status,
        "status_text": strategy.status_text or "",
    }


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


def _run_strategy_agent_job(app_obj, thread_id: str, model: str) -> None:
    def persist_status_text(text: str) -> None:
        t = (text or "")[:512]
        s = SessionLocal()
        try:
            row = s.get(Strategy, thread_id)
            if row is not None:
                row.status_text = t
                s.add(row)
                s.commit()
        finally:
            s.close()

    with app_obj.app_context():
        session = SessionLocal()
        try:
            strategy = session.get(Strategy, thread_id)
            if strategy is None:
                return
            messages = list(strategy.messages or [])
            canvas = dict(strategy.canvas or {})
            try:
                agent_result = build_agent_reply(
                    model=model,
                    messages=messages,
                    existing_canvas=canvas,
                    thread_id=thread_id,
                    on_progress=persist_status_text,
                )
                messages.append({"role": "assistant", "content": agent_result["message"]})
                strategy.messages = messages
                strategy.canvas = agent_result["canvas"]
                strategy.status = "success"
                strategy.status_text = ""
            except Exception as exc:
                strategy.status = "failure"
                strategy.status_text = str(exc)[:512]
            session.add(strategy)
            session.commit()
        finally:
            session.close()


@traceable(name="get_or_create_strategy")
def get_or_create_strategy(session: Session, thread_id: str) -> Strategy:
    strategy = session.get(Strategy, thread_id)
    if strategy is None:
        strategy = Strategy(thread_id=thread_id, messages=[], canvas={})
        session.add(strategy)
        session.flush()
    return strategy


@strategy_blueprint.get("/strategy")
@traceable(name="get_strategy")
def get_strategy() -> tuple:
    thread_id = request.args.get("thread_id", "").strip()
    if not thread_id:
        return _validation_error("thread_id query parameter is required")
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
        strategy = get_or_create_strategy(session, thread_id)
        if strategy.status == "running":
            session.commit()
            out = serialize_strategy(strategy)
            out["error"] = "A strategy update is already in progress."
            return jsonify(out), 409

        messages = list(strategy.messages or [])
        user_message = {"role": "user", "content": content}
        messages.append(user_message)
        strategy.messages = messages
        strategy.status = "running"
        strategy.status_text = "Starting…"
        session.add(strategy)
        session.commit()
        session.refresh(strategy)

        app_obj = current_app._get_current_object()
        model = app_obj.config["OPENROUTER_MODEL"]
        threading.Thread(
            target=_run_strategy_agent_job,
            args=(app_obj, thread_id, model),
            daemon=True,
        ).start()

        return jsonify(serialize_strategy(strategy)), 200
    finally:
        session.close()
