from __future__ import annotations

import json
import logging
import queue
import time
from datetime import date
from pathlib import Path

import pandas as pd
from flask import Blueprint, Response, g, jsonify, request

from application.queries.historical_bars import HistoricalBarsQuery, scale_to_timeframe
from application.schemas.simulation_dto import StartSimulationCommand
from application.services.simulation_limits import (
    simulation_date_span_error,
    simulation_start_validation_error,
)
from application.services.simulation_registry import SimulationRegistry
from application.use_cases.strategy_simulate import StrategySimulateCommandHandler
from auth import require_auth
from services.agent import thread_id_allowed

logger = logging.getLogger(__name__)

simulation_blueprint = Blueprint("simulation", __name__)

_registry = SimulationRegistry()
_bars_query = HistoricalBarsQuery()
_handler = StrategySimulateCommandHandler(_registry, _bars_query)


def _bad(message: str, code: int = 400) -> tuple:
    return jsonify({"error": message}), code


def _parse_iso_date(label: str, raw: str) -> date | None:
    t = (raw or "").strip()
    if not t:
        return None
    try:
        return date.fromisoformat(t)
    except ValueError:
        return None


_STRATEGY_PARAMS_PATH = Path(__file__).resolve().parents[1] / "strategies_v2" / "params.json"
_STRATEGIES_V2_ROOT = Path(__file__).resolve().parents[1] / "strategies_v2"


def _read_strategy_ticker() -> str:
    try:
        data = json.loads(_STRATEGY_PARAMS_PATH.read_text(encoding="utf-8"))
        t = data.get("ticker")
        if isinstance(t, str) and t.strip():
            return t.strip()
    except Exception:
        pass
    return "SPY"


def _row_unixtime(ts: object) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return int(t.timestamp())


def _bars_df_to_json_rows(df: pd.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for idx, row in df.iterrows():
        ut = _row_unixtime(idx)
        rows.append(
            {
                "unixtime": ut,
                "ohlc": {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                },
            }
        )
    return rows


def _parse_bps(raw: object) -> float:
    if isinstance(raw, str) and raw.strip().lower() == "max":
        return 1e6
    try:
        bps = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return max(0.1, min(bps, 1e6))


@simulation_blueprint.post("/simulation/start")
@require_auth
def simulation_start() -> tuple:
    uid = str(g.user_id)
    payload = request.get_json(silent=True) or {}
    thread_id = str(payload.get("thread_id", "")).strip()
    if not thread_id or not thread_id_allowed(thread_id):
        return _bad("invalid or missing thread_id")
    start_d = _parse_iso_date("start_date", str(payload.get("start_date", "")))
    end_d = _parse_iso_date("end_date", str(payload.get("end_date", "")))
    if start_d is None or end_d is None:
        return _bad("start_date and end_date are required (YYYY-MM-DD)")
    if start_d > end_d:
        return _bad("start_date must be on or before end_date")
    lim_err = simulation_start_validation_error(start_d, end_d)
    if lim_err:
        return _bad(lim_err)
    deposit = payload.get("initial_deposit", 10_000.0)
    try:
        initial_deposit = float(deposit)
    except (TypeError, ValueError):
        return _bad("initial_deposit must be a number")
    if initial_deposit <= 0:
        return _bad("initial_deposit must be positive")
    bps = _parse_bps(payload.get("initial_speed_bps", 1.0))
    sim_scale_raw = payload.get("simulation_scale")
    sim_scale: str | None = None
    if isinstance(sim_scale_raw, str) and sim_scale_raw.strip():
        try:
            scale_to_timeframe(sim_scale_raw)
        except ValueError:
            return _bad(f"unsupported simulation_scale {sim_scale_raw!r}")
        sim_scale = sim_scale_raw.strip().lower()
    cmd = StartSimulationCommand(
        user_id=uid,
        thread_id=thread_id,
        start_date=start_d,
        end_date=end_d,
        initial_speed_bps=bps,
        initial_deposit=initial_deposit,
        strategy_workspace=_STRATEGIES_V2_ROOT / thread_id,
        strategy_entry="strategy.py",
        simulation_scale=sim_scale,
    )
    try:
        _handler.start(cmd)
    except Exception as exc:
        logger.exception("simulation start failed", extra={"thread_id": thread_id})
        return _bad(str(exc)[:512], 500)
    return jsonify({"ok": True, "thread_id": thread_id}), 200


@simulation_blueprint.post("/simulation/pause")
@require_auth
def simulation_pause() -> tuple:
    uid = str(g.user_id)
    payload = request.get_json(silent=True) or {}
    thread_id = str(payload.get("thread_id", "")).strip()
    if not thread_id or not thread_id_allowed(thread_id):
        return _bad("invalid or missing thread_id")
    _handler.pause(uid, thread_id)
    return jsonify({"ok": True}), 200


@simulation_blueprint.post("/simulation/resume")
@require_auth
def simulation_resume() -> tuple:
    uid = str(g.user_id)
    payload = request.get_json(silent=True) or {}
    thread_id = str(payload.get("thread_id", "")).strip()
    if not thread_id or not thread_id_allowed(thread_id):
        return _bad("invalid or missing thread_id")
    _handler.resume(uid, thread_id)
    return jsonify({"ok": True}), 200


@simulation_blueprint.post("/simulation/speed")
@require_auth
def simulation_speed() -> tuple:
    uid = str(g.user_id)
    payload = request.get_json(silent=True) or {}
    thread_id = str(payload.get("thread_id", "")).strip()
    if not thread_id or not thread_id_allowed(thread_id):
        return _bad("invalid or missing thread_id")
    bps = _parse_bps(payload.get("bps", 1.0))
    try:
        _handler.change_speed(uid, thread_id, bps)
    except ValueError as exc:
        return _bad(str(exc))
    return jsonify({"ok": True, "bps": bps}), 200


@simulation_blueprint.get("/simulation/display_bars")
@require_auth
def simulation_display_bars() -> tuple:
    """Historical OHLC at ``scale`` for chart when user wants a finer TF than the strategy stream.

    Uses the same ticker as ``strategies_v2/params.json``. The calendar span is capped (same as
    simulation). If the estimated bar count for the full range exceeds the per-fetch budget, the
    host loads **multiple contiguous date windows** (each within the bar cap) and merges the
    result so the client still receives one ``bars`` array.
    """
    thread_id = request.args.get("thread_id", "").strip()
    if not thread_id or not thread_id_allowed(thread_id):
        return _bad("invalid or missing thread_id")
    scale = (request.args.get("scale") or "").strip().lower()
    try:
        scale_to_timeframe(scale)
    except ValueError:
        return _bad(f"unsupported scale {scale!r}")
    start_d = _parse_iso_date("start_date", str(request.args.get("start_date", "")))
    end_d = _parse_iso_date("end_date", str(request.args.get("end_date", "")))
    if start_d is None or end_d is None:
        return _bad("start_date and end_date are required (YYYY-MM-DD)")
    if start_d > end_d:
        return _bad("start_date must be on or before end_date")
    span_err = simulation_date_span_error(start_d, end_d)
    if span_err:
        return _bad(span_err)
    ticker = _read_strategy_ticker()
    try:
        merged, chunks_n = _bars_query.fetch_chunked_merge(
            ticker, scale, start_d, end_d, padding_days=0, provider=None
        )
    except Exception as exc:
        logger.exception("display_bars fetch failed", extra={"ticker": ticker, "scale": scale})
        return _bad(str(exc)[:512], 500)
    if merged.empty:
        return jsonify(
            {"ticker": ticker, "scale": scale, "bars": [], "chunks_fetched": chunks_n}
        ), 200
    return jsonify(
        {
            "ticker": ticker,
            "scale": scale,
            "bars": _bars_df_to_json_rows(merged),
            "chunks_fetched": chunks_n,
        }
    ), 200


@simulation_blueprint.post("/simulation/stop")
@require_auth
def simulation_stop() -> tuple:
    uid = str(g.user_id)
    payload = request.get_json(silent=True) or {}
    thread_id = str(payload.get("thread_id", "")).strip()
    if not thread_id or not thread_id_allowed(thread_id):
        return _bad("invalid or missing thread_id")
    _handler.stop(uid, thread_id)
    return jsonify({"ok": True}), 200


@simulation_blueprint.get("/simulation/stream")
@require_auth
def simulation_stream() -> tuple | Response:
    thread_id = request.args.get("thread_id", "").strip()
    if not thread_id or not thread_id_allowed(thread_id):
        return _bad("invalid or missing thread_id")
    uid = str(g.user_id)
    sess = _registry.get(uid, thread_id)
    if sess is None:
        return _bad("no active simulation for this thread", 404)

    def generate():
        last_keepalive = time.monotonic()
        while True:
            try:
                ev = sess.events.get(timeout=0.5)
            except queue.Empty:
                if (time.monotonic() - last_keepalive) >= 15:
                    yield ": keepalive\n\n"
                    last_keepalive = time.monotonic()
                continue
            if ev is None:
                break
            yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
            if ev.get("kind") == "status" and ev.get("status") == "done":
                break

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
