from __future__ import annotations

import json
import threading
import time

from datetime import datetime, timedelta, timezone
from flask import Blueprint, Response, current_app, g, jsonify, request, stream_with_context
import logging
from pathlib import Path
from sqlalchemy import desc, or_, text
from sqlalchemy.orm import defer
from auth import require_auth
from db.models import Strategy
from db.session import SessionLocal
from db.strategy_queries import (
    get_strategy_by_id,
    latest_thread_strategy,
)
from services.agent import (
    STRATEGIES_DIR,
    CHAT_MODEL,
    _TRACE_INPUTS,
    _TRACE_OUTPUTS,
    build_agent_reply,
    canvas_with_output,
    generate_strategy_algorithm_pseudocode,
    read_strategy_code,
    redact_secret_json_values_for_user,
    restore_strategy_workspace_from_snapshot,
    thread_id_allowed,
)
from services.strategy_stream_events import StrategyStreamPublisher, StrategyStreamSubscriber
from services.conversation_language import detect_conversation_language_iso
from services.supabase_trading_settings import fetch_user_timezone
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

strategy_blueprint = Blueprint("strategy", __name__)
logger = logging.getLogger(__name__)
STALE_RUNNING_AFTER = timedelta(hours=6)


def _sse_json(payload: dict, *, event: str | None = None, event_id: int | None = None) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if event:
        lines.append(f"event: {event}")
    data = json.dumps(payload, sort_keys=True)
    for line in data.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def _last_event_seq() -> int:
    raw = (request.headers.get("Last-Event-ID") or request.args.get("last_event_id") or "").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _stream_event_payload(event: dict) -> dict:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    payload = {
        "run_id": str(event.get("run_id") or ""),
        "seq": int(event.get("seq") or 0),
    }
    payload.update(data)
    return payload


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _running_cutoff() -> datetime:
    return (_now_utc() - STALE_RUNNING_AFTER).replace(tzinfo=None)


def _strategy_running_is_stale(strategy: Strategy) -> bool:
    if str(getattr(strategy, "status", "") or "") != "running":
        return False
    created_at = _as_utc(getattr(strategy, "created_at", None))
    if created_at is None:
        return False
    return _now_utc() - created_at > STALE_RUNNING_AFTER


def _strategy_status_fields(strategy: Strategy) -> tuple[str, str]:
    status = str(getattr(strategy, "status", "") or "")
    status_text = str(getattr(strategy, "status_text", "") or "")
    if status == "running" and _strategy_running_is_stale(strategy):
        return "failure", status_text or "Run did not finish."
    return status, status_text


def _messages_with_admin_extras(
    messages: list,
    strategy: Strategy,
    langsmith_traces: dict[str, str] | None = None,
) -> list:
    traces = dict(langsmith_traces or {})
    trace = str(strategy.langsmith_trace or "").strip()
    run_id = str(strategy.id or "").strip()
    if trace and run_id:
        traces.setdefault(run_id, trace)
    out: list = []
    for message in messages:
        if not isinstance(message, dict):
            out.append(message)
            continue
        item = dict(message)
        item.pop("langsmith_trace", None)
        message_run_id = str(item.get("run_id") or "").strip()
        if item.get("role") == "assistant" and message_run_id:
            message_trace = str(traces.get(message_run_id) or "").strip()
            if message_trace:
                item["langsmith_trace"] = message_trace
        out.append(item)
    return out


def _thread_langsmith_traces(session, thread_id: str) -> dict[str, str]:
    rows = (
        session.query(Strategy.id, Strategy.langsmith_trace)
        .filter(Strategy.thread_id == thread_id)
        .all()
    )
    traces: dict[str, str] = {}
    for run_id, trace in rows:
        rid = str(run_id or "").strip()
        url = str(trace or "").strip()
        if rid and url:
            traces[rid] = url
    return traces


def _messages_without_admin_extras(messages: list) -> list:
    out: list = []
    for message in messages:
        if not isinstance(message, dict):
            out.append(message)
            continue
        item = dict(message)
        item.pop("langsmith_trace", None)
        out.append(item)
    return out


def _merged_thread_messages(session, thread_id: str) -> list:
    rows = (
        session.query(Strategy)
        .options(defer(Strategy.canvas), defer(Strategy.code), defer(Strategy.algorithm))
        .filter(Strategy.thread_id == thread_id)
        .order_by(Strategy.created_at, Strategy.id)
        .all()
    )
    merged: list = []
    for row in rows:
        messages = list(row.messages or [])
        prefix_len = 0
        max_prefix_len = min(len(merged), len(messages))
        while prefix_len < max_prefix_len and merged[prefix_len] == messages[prefix_len]:
            prefix_len += 1
        merged.extend(messages[prefix_len:])
    return merged


def _current_user_id() -> str:
    return str(getattr(g, "user_id", "") or "").strip()


def _owned_or_legacy_filter(uid: str):
    return or_(Strategy.created_by == uid, Strategy.created_by.is_(None))


def _latest_thread_strategy_lightweight(session, thread_id: str) -> Strategy | None:
    return (
        session.query(Strategy)
        .options(defer(Strategy.canvas), defer(Strategy.code), defer(Strategy.algorithm))
        .filter(Strategy.thread_id == thread_id)
        .order_by(desc(Strategy.created_at), desc(Strategy.id))
        .first()
    )


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


def _thread_row_created_at(value) -> str | None:
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text_value = str(value).strip()
    if " " in text_value and "T" not in text_value:
        text_value = text_value.replace(" ", "T", 1)
    if text_value.endswith(".000000"):
        text_value = text_value[:-7]
    return text_value or None


def _serialize_thread_rows(rows, *, include_owner: bool = False) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        item = {
            "thread_id": row["thread_id"],
            "latest_run_id": row["id"],
            "latest_created_at": _thread_row_created_at(row["created_at"]),
            "message_count": int(row["messages_count"] or 0),
            "strategy_name": (row["strategy_name"] or "").strip()
            or "unknown strategy",
            "status": row["status"],
            "status_text": row["status_text"] or "",
        }
        if include_owner:
            item["created_by"] = (row["created_by"] or "").strip()
            item["created_by_email"] = (row["created_by_email"] or "").strip()
        out.append(item)
    return out


def _latest_threads_sql(where_clause: str = "", limit_clause: str = "") -> str:
    where = f"\n    {where_clause}" if where_clause else ""
    limit = f"\n{limit_clause}" if limit_clause else ""
    return f"""
SELECT id, thread_id, created_at, messages_count, status, status_text, strategy_name, created_by, created_by_email
FROM (
    SELECT
        id, thread_id, created_at, messages_count, status, status_text, strategy_name, created_by, created_by_email,
        ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY created_at DESC, id DESC) AS row_num
    FROM strategy{where}
) latest
WHERE row_num = 1
ORDER BY created_at DESC, id DESC{limit}
"""


def serialize_strategy(
    strategy: Strategy,
    *,
    langsmith_traces: dict[str, str] | None = None,
    include_canvas: bool = True,
    include_algorithm: bool = True,
    include_python_code: bool = True,
    messages_override: list | None = None,
) -> dict:
    is_admin = bool(g.is_admin)
    status, status_text = _strategy_status_fields(strategy)
    raw_messages = strategy.messages if messages_override is None else messages_override
    messages = redact_secret_json_values_for_user(list(raw_messages or []))
    if is_admin:
        messages = _messages_with_admin_extras(messages, strategy, langsmith_traces)
    else:
        messages = _messages_without_admin_extras(messages)
    payload = {
        "id": strategy.id,
        "thread_id": strategy.thread_id,
        "messages": messages,
        "status": status,
        "status_text": status_text,
        "langsmith_trace": (strategy.langsmith_trace or "") if is_admin else "",
        "strategy_name": strategy.strategy_name or "",
        "language": strategy.language or "",
        "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
    }
    if include_canvas:
        payload["canvas"] = redact_secret_json_values_for_user(dict(strategy.canvas or {}))
    if include_algorithm:
        payload["algorithm"] = strategy.algorithm or ""
    if is_admin and include_python_code:
        payload["python_code"] = strategy.code or ""
        payload["codex_thread_id"] = strategy.codex_thread_id or ""
    return payload


def serialize_strategy_canvas(strategy: Strategy) -> dict:
    is_admin = bool(g.is_admin)
    status, status_text = _strategy_status_fields(strategy)
    payload = {
        "id": strategy.id,
        "thread_id": strategy.thread_id,
        "canvas": redact_secret_json_values_for_user(dict(strategy.canvas or {})),
        "status": status,
        "status_text": status_text,
        "strategy_name": strategy.strategy_name or "",
        "algorithm": strategy.algorithm or "",
        "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
    }
    if is_admin:
        payload["python_code"] = strategy.code or ""
        payload["codex_thread_id"] = strategy.codex_thread_id or ""
    return payload


def _strategy_canvas_etag(strategy: Strategy) -> str:
    return str(strategy.id or "")


def _strategy_canvas_not_modified(strategy: Strategy) -> bool:
    etag = _strategy_canvas_etag(strategy)
    return bool(etag and request.if_none_match.contains_weak(etag))


def _apply_strategy_canvas_cache_headers(
    response: Response,
    strategy: Strategy,
    *,
    immutable: bool,
) -> Response:
    etag = _strategy_canvas_etag(strategy)
    if etag:
        response.set_etag(etag, weak=False)
    response.headers["Vary"] = "Authorization"
    response.headers["Cache-Control"] = (
        "private, max-age=31536000, immutable"
        if immutable
        else "private, no-cache"
    )
    return response


def _strategy_canvas_not_modified_response(strategy: Strategy, *, immutable: bool) -> Response:
    response = Response(status=304)
    return _apply_strategy_canvas_cache_headers(response, strategy, immutable=immutable)


def _strategy_canvas_response(strategy: Strategy, *, immutable: bool) -> Response:
    response = jsonify(serialize_strategy_canvas(strategy))
    return _apply_strategy_canvas_cache_headers(response, strategy, immutable=immutable)


def _include_strategy_heavy_fields() -> bool:
    raw = request.args.get("include_canvas", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@strategy_blueprint.get("/threads")
@require_auth
def list_threads() -> tuple:
    uid = str(g.user_id or "").strip()
    session = SessionLocal()
    try:
        sql = _latest_threads_sql("WHERE created_by = :uid")
        stmt = text(sql)
        logger.info("list_threads SQL: %s", sql.strip())
        rows = session.execute(stmt, {"uid": uid}).mappings().all()
        return (
            jsonify(
                {
                    "threads": _serialize_thread_rows(rows)
                }
            ),
            200,
        )
    finally:
        session.close()


@strategy_blueprint.get("/threads/recent")
@require_auth
def list_recent_threads() -> tuple:
    if not bool(g.is_admin):
        return jsonify({"error": "forbidden"}), 403

    uid = str(g.user_id or "").strip()
    session = SessionLocal()
    try:
        sql = _latest_threads_sql(
            "WHERE created_by IS NULL OR created_by <> :uid",
            "LIMIT :limit",
        )
        stmt = text(sql)
        logger.info("list_recent_threads SQL: %s", sql.strip())
        rows = session.execute(stmt, {"uid": uid, "limit": 10}).mappings().all()
        return jsonify({"threads": _serialize_thread_rows(rows, include_owner=True)}), 200
    finally:
        session.close()


@strategy_blueprint.delete("/threads/<thread_id>")
@require_auth
def delete_thread(thread_id: str) -> tuple:
    uid = _current_user_id()
    thread_id = (thread_id or "").strip()
    if not thread_id:
        return _validation_error("thread_id is required")
    if not thread_id_allowed(thread_id):
        return _validation_error("invalid thread_id")

    session = SessionLocal()
    try:
        deleted = (
            session.query(Strategy)
            .filter(Strategy.thread_id == thread_id, _owned_or_legacy_filter(uid))
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
            .filter(
                Strategy.thread_id == thread_id,
                Strategy.created_at > target.created_at,
            )
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


def _strategy_run_exists(run_id: str) -> bool:
    session = SessionLocal()
    try:
        return session.get(Strategy, run_id) is not None
    finally:
        session.close()


def _restore_thread_workspace_if_no_newer_run(
    thread_id: str,
    run_created_at,
) -> None:
    session = SessionLocal()
    try:
        latest = latest_thread_strategy(session, thread_id)
        if latest is None:
            return
        latest_created_at = getattr(latest, "created_at", None)
        if (
            run_created_at is not None
            and latest_created_at is not None
            and latest_created_at > run_created_at
        ):
            return
        code = getattr(latest, "code", "") or ""
        canvas = dict(getattr(latest, "canvas", {}) or {})
    finally:
        session.close()
    restore_strategy_workspace_from_snapshot(thread_id, code=code, canvas=canvas)


@traceable(name="post_strategy", process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
def _execute_strategy_agent_job(run_id: str, thread_id: str, user_timezone: str = "") -> None:
    stream = StrategyStreamPublisher(run_id)
    stream.status("Starting…")

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
        stream.status(t)

    session = SessionLocal()
    try:
        strategy = session.get(Strategy, run_id)
        if strategy is None:
            return
        messages = list(strategy.messages or [])
        canvas = dict(strategy.canvas or {})
        run_created_at = getattr(strategy, "created_at", None)
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
                on_token=stream.assistant_delta if stream.enabled else None,
                codex_thread_id=str(strategy.codex_thread_id or ""),
                user_timezone=user_timezone,
            )
            if not _strategy_run_exists(run_id):
                _restore_thread_workspace_if_no_newer_run(thread_id, run_created_at)
                stream.close()
                return
            stream.assistant_done()
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
            strategy.codex_thread_id = str(
                agent_result.get("codex_thread_id") or strategy.codex_thread_id or ""
            )[:128]
            sn = (
                str(agent_result.get("strategy_name") or "").strip()
                or _strategy_name_from_canvas(strategy.canvas)
            )
            if sn:
                strategy.strategy_name = sn[:512]
            strategy.status = "success"
            strategy.status_text = ""
        except Exception as exc:
            if not _strategy_run_exists(run_id):
                _restore_thread_workspace_if_no_newer_run(thread_id, run_created_at)
                stream.close()
                return
            logger.exception(
                "agent job failed",
                extra={"thread_id": thread_id, "run_id": run_id, "model": CHAT_MODEL},
            )
            strategy.status = "failure"
            strategy.status_text = str(exc)[:512]
            strategy.code = read_strategy_code(thread_id)
            stream.error(str(exc))
        _apply_langsmith_trace(strategy)
        session.add(strategy)
        try:
            session.commit()
        except Exception:
            session.rollback()
            if not _strategy_run_exists(run_id):
                _restore_thread_workspace_if_no_newer_run(thread_id, run_created_at)
                stream.close()
                return
            raise
    finally:
        session.close()


def _run_strategy_agent_job(app_obj, run_id: str, thread_id: str, user_timezone: str = "") -> None:
    with app_obj.app_context():
        _execute_strategy_agent_job(
            run_id,
            thread_id,
            langsmith_extra={"metadata": {"thread_id": thread_id}},
            user_timezone=user_timezone,
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
def get_strategy() -> tuple:
    uid = _current_user_id()
    include_heavy_fields = _include_strategy_heavy_fields()
    run_id = request.args.get("id", "").strip()
    if run_id:
        session = SessionLocal()
        try:
            if include_heavy_fields:
                strategy = get_strategy_by_id(session, run_id)
            else:
                strategy = (
                    session.query(Strategy)
                    .options(defer(Strategy.canvas), defer(Strategy.code), defer(Strategy.algorithm))
                    .filter(Strategy.id == run_id)
                    .first()
                )
            if strategy is None:
                return _validation_error("strategy not found")
            _stamp_langsmith_thread_metadata(strategy.thread_id)
            traces = (
                _thread_langsmith_traces(session, strategy.thread_id)
                if bool(g.is_admin)
                else None
            )
            return (
                jsonify(
                    serialize_strategy(
                        strategy,
                        langsmith_traces=traces,
                        include_canvas=include_heavy_fields,
                        include_algorithm=include_heavy_fields,
                        include_python_code=include_heavy_fields,
                    )
                ),
                200,
            )
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
        created_strategy = False
        strategy = (
            latest_thread_strategy(session, thread_id)
            if include_heavy_fields
            else _latest_thread_strategy_lightweight(session, thread_id)
        )
        if strategy is None:
            created_strategy = True
            strategy = Strategy(
                thread_id=thread_id,
                created_by=uid,
                created_by_email=getattr(g, "user_email", None),
                messages=[],
                canvas={},
            )
            session.add(strategy)
            session.flush()
        if include_heavy_fields or created_strategy:
            session.commit()
        if include_heavy_fields and needs_restore:
            restore_strategy_workspace_from_snapshot(
                thread_id,
                code=getattr(strategy, "code", "") or "",
                canvas=dict(getattr(strategy, "canvas", {}) or {}),
            )
        messages = _merged_thread_messages(session, thread_id)
        traces = _thread_langsmith_traces(session, thread_id) if bool(g.is_admin) else None
        return (
            jsonify(
                serialize_strategy(
                    strategy,
                    langsmith_traces=traces,
                    include_canvas=include_heavy_fields,
                    include_algorithm=include_heavy_fields,
                    include_python_code=include_heavy_fields,
                    messages_override=messages,
                )
            ),
            200,
        )
    finally:
        session.close()


@strategy_blueprint.get("/strategy/canvas")
@require_auth
def get_strategy_canvas() -> tuple:
    uid = _current_user_id()
    run_id = request.args.get("id", "").strip()
    if run_id:
        session = SessionLocal()
        try:
            cached_strategy = (
                session.query(Strategy)
                .options(defer(Strategy.canvas), defer(Strategy.code), defer(Strategy.algorithm))
                .filter(Strategy.id == run_id)
                .first()
            )
            if cached_strategy is None:
                return _validation_error("strategy not found")
            if _strategy_canvas_not_modified(cached_strategy):
                return _strategy_canvas_not_modified_response(cached_strategy, immutable=True)
            _stamp_langsmith_thread_metadata(cached_strategy.thread_id)
            strategy = get_strategy_by_id(session, run_id)
            return _strategy_canvas_response(strategy, immutable=True), 200
        finally:
            session.close()

    thread_id = request.args.get("thread_id", "").strip()
    if not thread_id:
        return _validation_error("thread_id or id query parameter is required")
    if not thread_id_allowed(thread_id):
        return _validation_error("invalid thread_id")

    session = SessionLocal()
    try:
        cached_strategy = _latest_thread_strategy_lightweight(session, thread_id)
        if cached_strategy is not None and _strategy_canvas_not_modified(cached_strategy):
            return _strategy_canvas_not_modified_response(cached_strategy, immutable=False)
        _stamp_langsmith_thread_metadata(thread_id)
        workspace = Path(STRATEGIES_DIR) / thread_id
        needs_restore = not workspace.is_dir()
        strategy = latest_thread_strategy(session, thread_id)
        if strategy is None:
            strategy = Strategy(
                thread_id=thread_id,
                created_by=uid,
                created_by_email=getattr(g, "user_email", None),
                messages=[],
                canvas={},
            )
            session.add(strategy)
            session.flush()
        session.commit()
        if needs_restore:
            restore_strategy_workspace_from_snapshot(
                thread_id,
                code=getattr(strategy, "code", "") or "",
                canvas=dict(getattr(strategy, "canvas", {}) or {}),
            )
        return _strategy_canvas_response(strategy, immutable=False), 200
    finally:
        session.close()


@strategy_blueprint.post("/strategy")
@require_auth
def post_strategy() -> tuple:
    uid = _current_user_id()
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
            .filter(
                Strategy.thread_id == thread_id,
                Strategy.status == "running",
                or_(Strategy.created_at.is_(None), Strategy.created_at >= _running_cutoff()),
            )
            .order_by(desc(Strategy.created_at))
            .first()
        )
        if running is not None:
            session.commit()
            out = serialize_strategy(
                running,
                messages_override=_merged_thread_messages(session, thread_id),
            )
            out["error"] = "A strategy update is already in progress."
            return jsonify(out), 409

        latest = latest_thread_strategy(session, thread_id)
        prev_messages = _merged_thread_messages(session, thread_id) if latest else []
        prev_canvas = dict(latest.canvas or {}) if latest else {}
        prev_code = getattr(latest, "code", "") if latest else ""
        prev_codex_thread_id = getattr(latest, "codex_thread_id", "") if latest else ""
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
            codex_thread_id=prev_codex_thread_id or "",
        )
        session.add(new_strategy)
        session.commit()
        session.refresh(new_strategy)

        run_id = new_strategy.id
        app_obj = current_app._get_current_object()
        user_tz = fetch_user_timezone(str(uid or ""))
        threading.Thread(
            target=_run_strategy_agent_job,
            args=(app_obj, run_id, thread_id, user_tz),
            daemon=True,
        ).start()

        return jsonify(serialize_strategy(new_strategy)), 200
    finally:
        session.close()


@strategy_blueprint.post("/strategy/algorithm")
@require_auth
@traceable(process_inputs=_TRACE_INPUTS, process_outputs=_TRACE_OUTPUTS)
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

    session = SessionLocal()
    try:
        current_strategy = latest_thread_strategy(session, thread_id)
        if current_strategy is None:
            return Response(status=204)
        status, _ = _strategy_status_fields(current_strategy)
        if status != "running":
            return Response(status=204)
    finally:
        session.close()

    def generate():
        last_snapshot = None
        last_keepalive = time.monotonic()
        subscriber: StrategyStreamSubscriber | None = None
        subscribed_run_id = ""
        after_seq = _last_event_seq()
        try:
            while True:
                session = SessionLocal()
                try:
                    strategy = latest_thread_strategy(session, thread_id)
                    if strategy is None:
                        break
                    run_id = str(strategy.id or "")
                    traces = (
                        _thread_langsmith_traces(session, thread_id)
                        if bool(g.is_admin)
                        else None
                    )
                    messages = _merged_thread_messages(session, thread_id)
                    if run_id and run_id != subscribed_run_id:
                        if subscriber is not None:
                            subscriber.close()
                        subscriber = StrategyStreamSubscriber(run_id, after_seq if not subscribed_run_id else 0)
                        subscribed_run_id = run_id
                    snapshot = json.dumps(
                        serialize_strategy(
                            strategy,
                            langsmith_traces=traces,
                            messages_override=messages,
                        ),
                        sort_keys=True,
                    )
                    if snapshot != last_snapshot:
                        last_snapshot = snapshot
                        yield f"data: {snapshot}\n\n"
                        last_keepalive = time.monotonic()
                    status, _ = _strategy_status_fields(strategy)
                    done = status != "running"
                finally:
                    session.close()
                if subscriber is not None:
                    for event in subscriber.drain(timeout=0.0):
                        yield _sse_json(
                            _stream_event_payload(event),
                            event=str(event.get("kind") or "strategy_event"),
                            event_id=int(event.get("seq") or 0),
                        )
                        last_keepalive = time.monotonic()
                if done:
                    break
                if subscriber is not None:
                    for event in subscriber.drain(timeout=0.5):
                        yield _sse_json(
                            _stream_event_payload(event),
                            event=str(event.get("kind") or "strategy_event"),
                            event_id=int(event.get("seq") or 0),
                        )
                        last_keepalive = time.monotonic()
                    if (time.monotonic() - last_keepalive) >= 15:
                        yield ": keepalive\n\n"
                        last_keepalive = time.monotonic()
                    continue
                if (time.monotonic() - last_keepalive) >= 15:
                    yield ": keepalive\n\n"
                    last_keepalive = time.monotonic()
                time.sleep(0.5)
        finally:
            if subscriber is not None:
                subscriber.close()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
