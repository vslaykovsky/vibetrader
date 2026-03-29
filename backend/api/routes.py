from __future__ import annotations

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
    }

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
def get_strategy() -> tuple[dict, int]:
    thread_id = request.args.get("thread_id", "").strip()
    if not thread_id:
        return {"error": "thread_id query parameter is required"}, 400
    if not thread_id_allowed(thread_id):
        return {"error": "invalid thread_id"}, 400

    session = SessionLocal()
    try:
        strategy = get_or_create_strategy(session, thread_id)
        session.commit()
        return jsonify(serialize_strategy(strategy)), 200
    finally:
        session.close()


@strategy_blueprint.post("/strategy")
@traceable(name="post_strategy")
def post_strategy() -> tuple[dict, int]:
    payload = request.get_json(silent=True) or {}
    thread_id = str(payload.get("thread_id", "")).strip()
    content = str(payload.get("message", "")).strip()

    if not thread_id:
        return {"error": "thread_id is required"}, 400
    if not thread_id_allowed(thread_id):
        return {"error": "invalid thread_id"}, 400
    if not content:
        return {"error": "message is required"}, 400

    session = SessionLocal()
    try:
        strategy = get_or_create_strategy(session, thread_id)
        messages = list(strategy.messages or [])
        canvas = dict(strategy.canvas or {})

        user_message = {"role": "user", "content": content}
        messages.append(user_message)

        agent_result = build_agent_reply(
            model=current_app.config["OPENROUTER_MODEL"],
            messages=messages,
            existing_canvas=canvas,
            thread_id=thread_id,
        )


        messages.append({"role": "assistant", "content": agent_result["message"]})
        strategy.messages = messages
        strategy.canvas = agent_result["canvas"]

        session.add(strategy)
        session.commit()
        session.refresh(strategy)

        return jsonify(serialize_strategy(strategy)), 200
    finally:
        session.close()
