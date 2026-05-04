from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

try:
    import dotenv

    dotenv.load_dotenv(_BACKEND_ROOT / ".env")
except Exception:
    pass

from alpaca.trading.client import TradingClient
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.stream import TradingStream
from sqlalchemy.exc import IntegrityError

from application.services.live_run_control import live_run_row_requests_stop
from application.services.alpaca_live_db import (
    LiveSubscriptionSpec,
    delete_runner_subscriptions,
    prune_stale_subscriptions,
    read_run_market_events_after,
    read_run_strategy_inputs_after,
    touch_runner_subscriptions,
    upsert_runner_subscriptions,
)
from application.queries.historical_bars import infer_asset_class
from application.services.indicators import IndicatorEngine
from application.services.scale_utils import (
    is_finer_or_equal,
    normalize_scale,
    scale_divides,
)
from application.services.simulation_driver import (
    RunningBar,
    RenkoState,
    SimulationStep,
    aggregate_to_base,
    assign_subscription_ids,
    compile_subscriptions,
    expand_step_to_lines,
    renko_brick_size_for_update,
)
from application.services.scale_utils import floor_ts_to_scale
from application.services.strategy_runtime import StrategyRuntime
from db.models import LiveRun, LiveRunEvent, LiveRunOrder, Strategy
from db.session import SessionLocal
from db.strategy_queries import resolve_strategy_row_for_live
from strategies_v2.utils import (
    InputIndicatorDataPoint,
    InputOhlcDataPoint,
    InputPortfolioDataPoint,
    InputRenkoDataPoint,
    Ohlc,
    OutputChart,
    OutputIndicatorDataPoint,
    OutputMarketTradeOrder,
    OutputTickerSubscription,
    OutputTimeAck,
    StrategyInput,
    StrategyOutput,
)

logger = logging.getLogger(__name__)

_SIMULATION_SCALE = "1m"
_USE_CASH_ONLY_FOR_BUY_NOTIONAL = True


def _materialize_workspace_from_db(strategy_row: Strategy, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "strategy.py").write_text(strategy_row.code or "", encoding="utf-8")
    canvas = strategy_row.canvas or {}
    output = canvas.get("output") if isinstance(canvas.get("output"), dict) else {}
    params_blob = output.get("params.json") if isinstance(output, dict) else None
    params_path = dest / "params.json"
    if isinstance(params_blob, dict):
        params_path.write_text(
            json.dumps(params_blob, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif isinstance(params_blob, str) and params_blob.strip():
        params_path.write_text(params_blob, encoding="utf-8")
    else:
        tmpl = _BACKEND_ROOT / "strategies_v2" / "params.json"
        if tmpl.is_file():
            shutil.copy2(tmpl, params_path)
        else:
            params_path.write_text("{}\n", encoding="utf-8")
    v2 = _BACKEND_ROOT / "strategies_v2"
    for name in ("utils.py", "hyperopt.py"):
        src = v2 / name
        if src.is_file():
            shutil.copy2(src, dest / name)


def _log_strategy_input(inp: StrategyInput) -> None:
    logger.info("input %s", inp.model_dump_json())


def _log_strategy_output(out: StrategyOutput) -> None:
    rest = [p for p in out.root if not isinstance(p, OutputTimeAck)]
    if not rest:
        return
    logger.info("output %s", StrategyOutput(rest).model_dump_json())


def _require_env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise RuntimeError(f"{name} must be set")
    return v


def _float_attr(obj, name: str, default: float = 0.0) -> float:
    try:
        return float(getattr(obj, name, default) or default)
    except (TypeError, ValueError):
        return float(default)


def _float_value(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if pd.notna(out) else None


def _enum_value(value) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _compact_alpaca_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper().replace("/", "")


def _strategy_symbol_for_alpaca_position(symbol: str, strategy_tickers: list[str] | None) -> str:
    sym = str(symbol or "").strip().upper()
    compact = _compact_alpaca_symbol(sym)
    for ticker in strategy_tickers or []:
        t = str(ticker or "").strip().upper()
        if t == sym or _compact_alpaca_symbol(t) == compact:
            return t
    return sym


def _alpaca_symbols_match(left: str, right: str) -> bool:
    l = str(left or "").strip().upper()
    r = str(right or "").strip().upper()
    return bool(l and r and (l == r or _compact_alpaca_symbol(l) == _compact_alpaca_symbol(r)))


def _alpaca_position_qty_from_positions(positions, symbol: str) -> float:
    for p in positions:
        if _alpaca_symbols_match(str(getattr(p, "symbol", "")), symbol):
            return _float_attr(p, "qty")
    return 0.0


def _dt_iso(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _dt_unixtime(value) -> int | None:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if getattr(ts, "tzinfo", None) is None and getattr(ts, "tz", None) is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return int(ts.timestamp())


def _alpaca_client(*, paper: bool) -> TradingClient:
    return TradingClient(
        api_key=_require_env("ALPACA_API_KEY"),
        secret_key=_require_env("ALPACA_SECRET_KEY"),
        paper=paper,
    )


def _startup_ticker_rows(startup: StrategyOutput) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for p in startup.root:
        if isinstance(p, OutputTickerSubscription):
            rows.append((p.ticker.strip().upper(), normalize_scale(p.scale)))
    return rows


def _subscribed_tickers_and_base_scale(startup: StrategyOutput) -> tuple[list[str], str]:
    rows = _startup_ticker_rows(startup)
    if not rows:
        raise ValueError("Strategy startup did not include ticker_subscription")
    scales = {s for _, s in rows}
    if len(scales) != 1:
        raise ValueError(f"All ticker_subscription entries must use the same scale; got {rows!r}")
    tickers = list(dict.fromkeys(t for t, _ in rows))
    return tickers, next(iter(scales))


def _subscription_specs_for_live_bars(startup: StrategyOutput) -> list[LiveSubscriptionSpec]:
    tickers, _base_scale = _subscribed_tickers_and_base_scale(startup)
    return [LiveSubscriptionSpec(channel="bars", symbol=t, scale=_SIMULATION_SCALE) for t in tickers]


def _portfolio_snapshot_from_alpaca(
    client: TradingClient,
    *,
    strategy_tickers: list[str] | None = None,
) -> InputPortfolioDataPoint:
    acct = client.get_account()
    cash = max(0.0, _float_attr(acct, "cash"))
    equity = _float_attr(acct, "equity", cash)
    buying_power = max(0.0, _float_attr(acct, "buying_power", cash))
    positions = []
    for p in client.get_all_positions():
        sym = str(getattr(p, "symbol", "")).strip().upper()
        qty = float(getattr(p, "qty", 0.0) or 0.0)
        avg = float(getattr(p, "avg_entry_price", 0.0) or 0.0)
        if not sym or qty == 0:
            continue
        fallback_value = abs(qty) * avg
        market_value = abs(_float_attr(p, "market_value", fallback_value))
        deposit_ratio = market_value / equity if equity > 0 else 0.0
        positions.append(
            {
                "ticker": _strategy_symbol_for_alpaca_position(sym, strategy_tickers),
                "order_type": "long" if qty > 0 else "short",
                "deposit_ratio": max(0.0, min(1.0, float(deposit_ratio))),
                "volume_weighted_avg_entry_price": float(avg),
            }
        )
    return InputPortfolioDataPoint(
        cash=cash,
        equity=equity,
        buying_power=buying_power,
        positions=positions,
    )


def _fires_on(driver_ts: pd.Timestamp, next_ts: pd.Timestamp | None, update_scale: str) -> bool:
    cur = floor_ts_to_scale(driver_ts, update_scale)
    if next_ts is None:
        return True
    nxt = floor_ts_to_scale(next_ts, update_scale)
    return nxt != cur


def _step_from_driver_index(
    *,
    driver_df: pd.DataFrame,
    base_df: pd.DataFrame,
    base_scale: str,
    simulation_scale: str,
    j: int,
    bucket_to_row: dict[pd.Timestamp, int],
    running: RunningBar | None,
    cur_base_idx: int | None,
    ticker_subs,
    indicator_subs,
    renko_subs,
    indicator_engine: IndicatorEngine,
    renko_states: list[RenkoState],
) -> tuple[SimulationStep | None, RunningBar | None, int | None]:
    if j < 0 or j >= len(driver_df):
        return None, running, cur_base_idx
    driver_ts = pd.Timestamp(driver_df.index[j])
    if getattr(driver_ts, "tzinfo", None) is None:
        driver_ts = driver_ts.tz_localize("UTC")
    next_ts = pd.Timestamp(driver_df.index[j + 1]) if j + 1 < len(driver_df) else None
    if next_ts is not None and getattr(next_ts, "tzinfo", None) is None:
        next_ts = next_ts.tz_localize("UTC")
    base_ts = floor_ts_to_scale(driver_ts, base_scale)
    base_row = bucket_to_row.get(base_ts)
    if base_row is None:
        return None, running, cur_base_idx

    row = driver_df.iloc[j]
    o, h, l, c = (
        float(row["open"]),
        float(row["high"]),
        float(row["low"]),
        float(row["close"]),
    )
    v = float(row["volume"]) if "volume" in driver_df.columns else 0.0
    if cur_base_idx != base_row or running is None:
        running = RunningBar(open=o, high=h, low=l, close=c, volume=v)
        cur_base_idx = base_row
    else:
        if h > running.high:
            running.high = h
        if l < running.low:
            running.low = l
        running.close = c
        running.volume += v

    is_base_close = _fires_on(driver_ts, next_ts, base_scale)
    next_unix = int(next_ts.timestamp()) if next_ts is not None else None
    step = SimulationStep(
        driver_index=j,
        driver_ts=driver_ts,
        unixtime=int(driver_ts.timestamp()),
        base_row=base_row,
        base_ts=base_ts,
        running=RunningBar(**running.__dict__),
        is_base_close=is_base_close,
        next_driver_unixtime=next_unix,
    )

    for ts in ticker_subs:
        if _fires_on(driver_ts, next_ts, ts.update_scale):
            step.ticker_points.append(
                InputOhlcDataPoint(
                    id=ts.id,
                    ticker=ts.ticker,
                    ohlc=Ohlc(
                        open=running.open,
                        high=running.high,
                        low=running.low,
                        close=running.close,
                        volume=running.volume,
                    ),
                    closed=is_base_close,
                )
            )

    for ind_i, ind_spec in enumerate(indicator_subs):
        if not _fires_on(driver_ts, next_ts, ind_spec.update_scale):
            continue
        sub_index = ind_i
        if is_base_close:
            for pt in indicator_engine.values_at_row_for_subscription(sub_index, base_row):
                step.indicator_points.append(pt.model_copy(update={"id": ind_spec.id}))
        else:
            for pt in indicator_engine.partial_values_at_row_for_subscription(
                sub_index,
                base_row,
                partial_close=running.close,
                partial_high=running.high,
                partial_low=running.low,
            ):
                step.indicator_points.append(pt.model_copy(update={"id": ind_spec.id}))

    for ri, rspec in enumerate(renko_subs):
        if not _fires_on(driver_ts, next_ts, rspec.update_scale):
            continue
        src = rspec.source
        brick_size = renko_brick_size_for_update(
            src, base_df, base_row, running, is_base_close
        )
        if brick_size is None:
            continue
        st = renko_states[ri]
        price = running.close
        if st.anchor is None:
            st.anchor = price
            continue
        while price >= st.anchor + brick_size:
            brick_open = st.anchor
            brick_close = brick_open + brick_size
            step.renko_points.append(
                InputRenkoDataPoint(
                    id=rspec.id,
                    ticker=rspec.ticker,
                    brick_size=brick_size,
                    open=brick_open,
                    close=brick_close,
                    direction="up",
                )
            )
            st.anchor = brick_close
        while price <= st.anchor - brick_size:
            brick_open = st.anchor
            brick_close = brick_open - brick_size
            step.renko_points.append(
                InputRenkoDataPoint(
                    id=rspec.id,
                    ticker=rspec.ticker,
                    brick_size=brick_size,
                    open=brick_open,
                    close=brick_close,
                    direction="down",
                )
            )
            st.anchor = brick_close

    if step.renko_points:
        # Build partial snapshot inline (same as simulation_driver)
        snap: list[InputOhlcDataPoint | InputIndicatorDataPoint] = []
        for sub in ticker_subs:
            src = sub.source
            if isinstance(src, OutputTickerSubscription) and src.partial:
                snap.append(
                    InputOhlcDataPoint(
                        id=sub.id,
                        ticker=src.ticker,
                        ohlc=Ohlc(
                            open=running.open,
                            high=running.high,
                            low=running.low,
                            close=running.close,
                            volume=running.volume,
                        ),
                        closed=False,
                    )
                )
        for i, sub in enumerate(indicator_subs):
            src = sub.source
            if not getattr(src, "partial", False):
                continue
            for pt in indicator_engine.partial_values_at_row_for_subscription(
                i,
                base_row,
                partial_close=running.close,
                partial_high=running.high,
                partial_low=running.low,
            ):
                snap.append(pt.model_copy(update={"id": sub.id}))
        step.partial_snapshot = snap

    step.fired = bool(step.ticker_points or step.indicator_points or step.renko_points)
    return step, running, cur_base_idx


def _event_type_for_kind(kind: str) -> str:
    if kind in {"input", "bar", "indicator_in", "portfolio", "renko", "market_bar"}:
        return "input"
    if kind in {"status", "startup"}:
        return "system"
    return "output"


def _emit_db_event(
    session,
    *,
    run_id: str,
    kind: str,
    unixtime: int | None,
    payload: dict,
    event_type: str | None = None,
) -> None:
    session.add(
        LiveRunEvent(
            run_id=run_id,
            event_type=event_type or _event_type_for_kind(str(kind)),
            kind=str(kind),
            unixtime=int(unixtime) if unixtime is not None else None,
            payload=dict(payload or {}),
        )
    )


def _client_order_id_for_signal(run_id: str, unixtime: int, order: OutputMarketTradeOrder) -> str:
    t = str(order.ticker).strip().upper()
    d = str(order.direction).strip().lower()
    dr = float(getattr(order, "deposit_ratio", 1.0) or 1.0)
    return f"{run_id}:{unixtime}:{t}:{d}:{dr:.6f}"


def _alpaca_api_error_details(exc: APIError) -> dict[str, object]:
    raw = str(exc)
    details: dict[str, object] = {"error": raw}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    message = str(parsed.get("message") or "").strip()
    code = str(parsed.get("code") or "").strip()
    status_code = exc.status_code
    if message:
        details["error"] = message
        details["alpaca_error_message"] = message
    if code:
        details["alpaca_error_code"] = code
    if status_code is not None:
        try:
            details["alpaca_status_code"] = int(status_code)
        except (TypeError, ValueError):
            details["alpaca_status_code"] = str(status_code)
    if raw and raw != details.get("error"):
        details["alpaca_error_raw"] = raw
    return details


def _alpaca_rejection_comment(payload: dict[str, object]) -> str:
    error = str(payload.get("alpaca_error_message") or payload.get("error") or "").strip()
    if not error:
        return ""
    code = str(payload.get("alpaca_error_code") or "").strip()
    status_code = str(payload.get("alpaca_status_code") or "").strip()
    detail = f"Alpaca rejected order: {error}"
    suffix = ", ".join(
        x
        for x in (
            f"code {code}" if code else "",
            f"HTTP {status_code}" if status_code else "",
        )
        if x
    )
    return f"{detail} ({suffix})" if suffix else detail


def _add_rejection_comment(payload: dict[str, object]) -> dict[str, object]:
    detail = _alpaca_rejection_comment(payload)
    if not detail:
        return payload
    existing = str(payload.get("short_explanation") or payload.get("reason") or "").strip()
    if detail in existing:
        return payload
    payload["short_explanation"] = f"{existing}; {detail}" if existing else detail
    return payload


def _time_in_force_for_symbol(symbol: str, *, session) -> TimeInForce:
    asset = infer_asset_class(symbol, provider="alpaca", session=session)
    return TimeInForce.GTC if asset == "crypto" else TimeInForce.DAY


def _submit_market_order(client: TradingClient, req: MarketOrderRequest) -> tuple[str, dict[str, object]]:
    try:
        placed = client.submit_order(req)
    except APIError as exc:
        return "", _alpaca_api_error_details(exc)
    return str(getattr(placed, "id", "") or ""), {}


def _order_update_payload_from_alpaca(update_or_order) -> dict[str, object]:
    order = getattr(update_or_order, "order", update_or_order)
    event = _enum_value(getattr(update_or_order, "event", ""))
    timestamp = getattr(update_or_order, "timestamp", None) or getattr(order, "updated_at", None)
    filled_qty = _float_value(getattr(order, "filled_qty", None))
    filled_avg_price = _float_value(getattr(order, "filled_avg_price", None))
    event_qty = _float_value(getattr(update_or_order, "qty", None))
    event_price = _float_value(getattr(update_or_order, "price", None))
    payload: dict[str, object] = {
        "ticker": str(getattr(order, "symbol", "") or "").strip().upper(),
        "direction": _enum_value(getattr(order, "side", "")),
        "action": event,
        "status": _enum_value(getattr(order, "status", "")) or event,
        "alpaca_order_id": str(getattr(order, "id", "") or ""),
        "client_order_id": str(getattr(order, "client_order_id", "") or ""),
        "filled_qty": filled_qty,
        "filled_avg_price": filled_avg_price,
        "qty": filled_qty if filled_qty is not None else event_qty,
        "price": filled_avg_price if filled_avg_price is not None else event_price,
        "broker_event": event,
        "submitted_at": _dt_iso(getattr(order, "submitted_at", None)),
        "filled_at": _dt_iso(getattr(order, "filled_at", None)),
        "updated_at": _dt_iso(getattr(order, "updated_at", None)),
    }
    unixtime = _dt_unixtime(timestamp)
    if unixtime is not None:
        payload["unixtime"] = unixtime
    return payload


def _live_order_update_changed(row: LiveRunOrder, payload: dict[str, object]) -> bool:
    status = str(payload.get("status") or "").strip()
    alpaca_order_id = str(payload.get("alpaca_order_id") or "").strip()
    filled_qty = _float_value(payload.get("filled_qty"))
    filled_avg_price = _float_value(payload.get("filled_avg_price"))
    position_before_order = _float_value(payload.get("position_before_order"))
    position_after_order_filled = _float_value(payload.get("position_after_order_filled"))
    return (
        (bool(status) and status != (row.status or ""))
        or (bool(alpaca_order_id) and alpaca_order_id != (row.alpaca_order_id or ""))
        or filled_qty != row.filled_qty
        or filled_avg_price != row.filled_avg_price
        or position_before_order != row.position_before_order
        or position_after_order_filled != row.position_after_order_filled
    )


def _record_order_update(session, *, run_id: str, payload: dict[str, object]) -> bool:
    client_order_id = str(payload.get("client_order_id") or "").strip()
    alpaca_order_id = str(payload.get("alpaca_order_id") or "").strip()
    query = session.query(LiveRunOrder).filter(LiveRunOrder.run_id == run_id)
    row = None
    if client_order_id:
        row = query.filter(LiveRunOrder.client_order_id == client_order_id).one_or_none()
    if row is None and alpaca_order_id:
        row = query.filter(LiveRunOrder.alpaca_order_id == alpaca_order_id).one_or_none()
    if row is None:
        return False
    position_before_order = _float_value(payload.get("position_before_order"))
    if position_before_order is None:
        position_before_order = row.position_before_order
        if position_before_order is not None:
            payload["position_before_order"] = position_before_order
    position_after_order_filled = _float_value(payload.get("position_after_order_filled"))
    if position_after_order_filled is None:
        filled_qty = _float_value(payload.get("filled_qty"))
        direction = str(payload.get("direction") or "").strip().lower()
        if position_before_order is not None and filled_qty is not None:
            qty = abs(float(filled_qty))
            if direction == "buy":
                position_after_order_filled = float(position_before_order) + qty
            elif direction == "sell":
                position_after_order_filled = float(position_before_order) - qty
            if position_after_order_filled is not None:
                payload["position_after_order_filled"] = position_after_order_filled
        elif row.position_after_order_filled is not None:
            position_after_order_filled = row.position_after_order_filled
            payload["position_after_order_filled"] = position_after_order_filled
    if not _live_order_update_changed(row, payload):
        return False
    status = str(payload.get("status") or "").strip()
    if status:
        row.status = status
    if alpaca_order_id:
        row.alpaca_order_id = alpaca_order_id
    row.filled_qty = _float_value(payload.get("filled_qty"))
    row.filled_avg_price = _float_value(payload.get("filled_avg_price"))
    row.position_before_order = _float_value(payload.get("position_before_order"))
    row.position_after_order_filled = _float_value(payload.get("position_after_order_filled"))
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    _emit_db_event(
        session,
        run_id=run_id,
        kind="order_update",
        unixtime=int(payload.get("unixtime") or time.time()),
        payload=payload,
    )
    return True


def _order_status_terminal(status: str) -> bool:
    return str(status or "").strip().lower() in {
        "filled",
        "canceled",
        "cancelled",
        "expired",
        "rejected",
        "replaced",
    }


def _reconcile_live_run_orders(client: TradingClient, *, session, run_id: str) -> int:
    rows = (
        session.query(LiveRunOrder)
        .filter(LiveRunOrder.run_id == run_id, LiveRunOrder.alpaca_order_id != "")
        .order_by(LiveRunOrder.created_at.desc())
        .limit(200)
        .all()
    )
    changed = 0
    for row in rows:
        if _order_status_terminal(row.status):
            continue
        try:
            order = client.get_order_by_id(row.alpaca_order_id)
        except APIError:
            logger.exception("failed to reconcile Alpaca order %s", row.alpaca_order_id)
            continue
        if _record_order_update(
            session,
            run_id=run_id,
            payload=_order_update_payload_from_alpaca(order),
        ):
            changed += 1
    return changed


class _AlpacaOrderUpdateStream:
    def __init__(self, *, run_id: str, paper: bool):
        self.run_id = run_id
        self.paper = paper
        self.stream: TradingStream | None = None
        self.thread: threading.Thread | None = None
        self.stopping = False

    def start(self) -> None:
        try:
            self.stream = TradingStream(
                _require_env("ALPACA_API_KEY"),
                _require_env("ALPACA_SECRET_KEY"),
                paper=self.paper,
            )

            async def handle(update) -> None:
                self.handle_update(update)

            self.stream.subscribe_trade_updates(handle)
            self.thread = threading.Thread(target=self.run, name=f"alpaca-order-updates-{self.run_id}", daemon=True)
            self.thread.start()
        except Exception:
            logger.exception("failed to start Alpaca order update stream")

    def run(self) -> None:
        try:
            if self.stream is not None:
                self.stream.run()
        except Exception:
            if not self.stopping:
                logger.exception("Alpaca order update stream stopped unexpectedly")

    def handle_update(self, update) -> None:
        payload = _order_update_payload_from_alpaca(update)
        client_order_id = str(payload.get("client_order_id") or "")
        alpaca_order_id = str(payload.get("alpaca_order_id") or "")
        if not client_order_id and not alpaca_order_id:
            return
        with SessionLocal() as session:
            if _record_order_update(session, run_id=self.run_id, payload=payload):
                session.commit()

    def stop(self) -> None:
        self.stopping = True
        if self.stream is not None:
            try:
                self.stream.stop()
            except Exception:
                logger.exception("failed to stop Alpaca order update stream")
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=5.0)


def _maybe_execute_market_order(
    client: TradingClient,
    *,
    session,
    run_id: str,
    unixtime: int,
    order: OutputMarketTradeOrder,
    enable_trading: bool,
) -> dict[str, object] | None:
    if not enable_trading:
        return None
    sym = str(order.ticker).strip().upper()
    side = OrderSide.BUY if str(order.direction).lower() == "buy" else OrderSide.SELL
    dr = float(getattr(order, "deposit_ratio", 1.0) or 1.0)
    dr = max(0.0, min(1.0, dr))
    cid = _client_order_id_for_signal(run_id, int(unixtime), order)
    positions = None

    def get_positions():
        nonlocal positions
        if positions is None:
            positions = client.get_all_positions()
        return positions

    try:
        position_before_order = _alpaca_position_qty_from_positions(get_positions(), sym)
        with session.begin_nested():
            row = LiveRunOrder(
                run_id=run_id,
                client_order_id=cid,
                position_before_order=position_before_order,
            )
            session.add(row)
            session.flush()
            if side == OrderSide.BUY:
                acct = client.get_account()
                cash = max(0.0, _float_attr(acct, "cash"))
                buying_power = max(0.0, _float_attr(acct, "buying_power", cash))
                basis = cash if _USE_CASH_ONLY_FOR_BUY_NOTIONAL else min(cash, buying_power)
                notional = round(basis * dr, 2)
                if notional <= 0:
                    row.status = "skipped"
                    row.position_after_order_filled = position_before_order
                    row.updated_at = datetime.now(timezone.utc)
                    return {
                        "client_order_id": cid,
                        "alpaca_order_id": "",
                        "status": "skipped",
                        "reason": "insufficient buying power",
                        "cash": cash,
                        "buying_power": buying_power,
                        "position_before_order": position_before_order,
                        "position_after_order_filled": position_before_order,
                    }
                req = MarketOrderRequest(
                    symbol=sym,
                    notional=notional,
                    side=side,
                    time_in_force=_time_in_force_for_symbol(sym, session=session),
                    client_order_id=cid,
                )
                aid, err = _submit_market_order(client, req)
                row.alpaca_order_id = aid
                if err:
                    row.status = "rejected"
                    row.position_after_order_filled = position_before_order
                    row.updated_at = datetime.now(timezone.utc)
                    return {
                        "client_order_id": cid,
                        "alpaca_order_id": "",
                        "status": "rejected",
                        "notional": notional,
                        "cash": cash,
                        "buying_power": buying_power,
                        "position_before_order": position_before_order,
                        "position_after_order_filled": position_before_order,
                    } | err
                row.status = "submitted"
                row.updated_at = datetime.now(timezone.utc)
                return {
                    "client_order_id": cid,
                    "alpaca_order_id": aid,
                    "status": "submitted",
                    "notional": notional,
                    "cash": cash,
                    "buying_power": buying_power,
                    "position_before_order": position_before_order,
                }
            pos = None
            for p in get_positions():
                if _alpaca_symbols_match(str(getattr(p, "symbol", "")), sym):
                    pos = p
                    break
            if pos is None:
                row.status = "skipped"
                row.position_after_order_filled = position_before_order
                row.updated_at = datetime.now(timezone.utc)
                return {
                    "client_order_id": cid,
                    "alpaca_order_id": "",
                    "status": "skipped",
                    "reason": "no open position",
                    "position_before_order": position_before_order,
                    "position_after_order_filled": position_before_order,
                }
            qty = float(getattr(pos, "qty", 0.0) or 0.0)
            sell_qty = abs(qty) * dr
            if sell_qty <= 0:
                row.status = "skipped"
                row.position_after_order_filled = position_before_order
                row.updated_at = datetime.now(timezone.utc)
                return {
                    "client_order_id": cid,
                    "alpaca_order_id": "",
                    "status": "skipped",
                    "reason": "zero sell quantity",
                    "position_before_order": position_before_order,
                    "position_after_order_filled": position_before_order,
                }
            req = MarketOrderRequest(
                symbol=sym,
                qty=sell_qty,
                side=side,
                time_in_force=_time_in_force_for_symbol(sym, session=session),
                client_order_id=cid,
            )
            aid, err = _submit_market_order(client, req)
            row.alpaca_order_id = aid
            if err:
                row.status = "rejected"
                row.position_after_order_filled = position_before_order
                row.updated_at = datetime.now(timezone.utc)
                return {
                    "client_order_id": cid,
                    "alpaca_order_id": "",
                    "status": "rejected",
                    "qty": sell_qty,
                    "position_before_order": position_before_order,
                    "position_after_order_filled": position_before_order,
                } | err
            row.status = "submitted"
            row.updated_at = datetime.now(timezone.utc)
            return {
                "client_order_id": cid,
                "alpaca_order_id": aid,
                "status": "submitted",
                "qty": sell_qty,
                "position_before_order": position_before_order,
            }
    except IntegrityError:
        return {
            "client_order_id": cid,
            "alpaca_order_id": "",
            "position_before_order": position_before_order,
        }


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(
        description="Run a strategies_v2 strategy against live Alpaca events stored in DB, and optionally place trades via Alpaca Trading API."
    )
    parser.add_argument(
        "--entry",
        default="",
        help="Path to strategy entry script; if omitted, use --thread-id and load strategy from the database.",
    )
    parser.add_argument(
        "--thread-id",
        default="",
        help="Chat thread UUID; strategy code is loaded from the latest strategy row (or --strategy-id).",
    )
    parser.add_argument(
        "--strategy-id",
        default="",
        dest="strategy_id",
        help="Optional primary key (id) of the strategy table row whose code column supplies strategy.py.",
    )
    parser.add_argument("--paper", action="store_true", help="Use Alpaca paper trading.")
    parser.add_argument("--enable-trading", action="store_true", help="Actually submit Alpaca orders.")
    parser.add_argument("--poll-ms", type=int, default=250, help="DB poll interval in milliseconds.")
    parser.add_argument("--subs-ttl-s", type=int, default=60, help="Subscription TTL in seconds.")
    parser.add_argument("--runner-id", default="", help="Optional stable runner id (default: random).")
    parser.add_argument("--run-id", default="", help="Optional live run id (default: random UUID).")
    parser.add_argument("--created-by", default="", help="Optional user id for LiveRun row.")
    parser.add_argument("--created-by-email", default="", help="Optional email for LiveRun row.")
    parser.add_argument(
        "--stop-poll-s",
        type=float,
        default=1.0,
        help="How often to read live_runs.status for stopping/stopped (seconds).",
    )
    parser.add_argument(
        "--order-reconcile-s",
        type=float,
        default=30.0,
        help="How often to reconcile submitted Alpaca orders by REST (seconds).",
    )
    args = parser.parse_args(argv)

    run_id = (str(args.run_id).strip() or str(uuid.uuid4())).strip()
    runner_id = (str(args.runner_id).strip() or run_id)[:64]
    entry_raw = (args.entry or "").strip()
    thread_raw = (args.thread_id or "").strip()
    strategy_id_raw = (args.strategy_id or "").strip()
    temp_workspace: Path | None = None
    if not entry_raw:
        if not thread_raw and run_id:
            with SessionLocal() as session:
                lr0 = session.get(LiveRun, run_id)
                if lr0 is not None:
                    thread_raw = (lr0.thread_id or "").strip()
                    if not strategy_id_raw:
                        strategy_id_raw = (lr0.deployed_from_run_id or "").strip()
        if not thread_raw:
            parser.error("provide --entry, or --thread-id, or --run-id for an existing live_runs row")
        with SessionLocal() as session:
            strat_row, strat_err = resolve_strategy_row_for_live(
                session,
                thread_id=thread_raw,
                strategy_id=strategy_id_raw,
            )
            if strat_err or strat_row is None:
                parser.error(strat_err or "strategy not found")
        temp_workspace = Path(tempfile.mkdtemp(prefix=f"live_run_{run_id}_"))
        _materialize_workspace_from_db(strat_row, temp_workspace)
        workspace = temp_workspace
        entry_script = "strategy.py"
        entry_path = workspace / entry_script
        thread_id_eff = thread_raw
        stored_entry_path = ""
    else:
        entry_path = Path(entry_raw).resolve()
        if not entry_path.is_file():
            parser.error(f"--entry must be an existing file: {entry_path}")
        workspace = entry_path.parent
        entry_script = entry_path.relative_to(workspace).as_posix()
        thread_id_eff = (args.thread_id or "").strip() or workspace.name
        stored_entry_path = str(entry_path)

    client = _alpaca_client(paper=bool(args.paper))
    rt = StrategyRuntime(workspace, entry_script=entry_script)
    last_event_id = 0
    last_touch = 0.0
    touch_every_s = 10.0
    enable_trading = bool(args.enable_trading)
    order_stream: _AlpacaOrderUpdateStream | None = None

    logger.info(
        "start run_id=%s runner_id=%s workspace=%s entry=%s paper=%s enable_trading=%s poll_ms=%s",
        run_id,
        runner_id,
        workspace,
        entry_script,
        bool(args.paper),
        enable_trading,
        int(args.poll_ms),
    )

    try:
        with SessionLocal() as session:
            row = session.get(LiveRun, run_id)
            if row is None:
                rb = (os.environ.get("LIVE_RUNNER_BACKEND") or "local").strip() or "local"
                row = LiveRun(
                    id=run_id,
                    thread_id=thread_id_eff,
                    created_by=(str(args.created_by).strip() or None),
                    created_by_email=(str(args.created_by_email).strip() or None),
                    mode="paper" if bool(args.paper) else "live",
                    status="running",
                    status_text="starting",
                    entry_path=stored_entry_path,
                    runner_backend=rb,
                    runner_id=runner_id,
                    last_input_event_id=0,
                )
                session.add(row)
            else:
                row.thread_id = thread_id_eff
                row.mode = "paper" if bool(args.paper) else "live"
                row.status = "running"
                row.status_text = "starting"
                row.entry_path = stored_entry_path
                row.runner_id = runner_id
                cb = str(args.created_by).strip()
                if cb:
                    row.created_by = cb
                cbe = str(args.created_by_email).strip()
                if cbe:
                    row.created_by_email = cbe
                session.add(row)
            _emit_db_event(
                session,
                run_id=run_id,
                kind="status",
                unixtime=int(time.time()),
                payload={"status": "starting"},
            )
            session.commit()

        startup = rt.start()
        startup = assign_subscription_ids(startup)
        _log_strategy_output(startup)

        tickers, base_scale = _subscribed_tickers_and_base_scale(startup)
        if len(tickers) != 1:
            raise RuntimeError("live runner currently supports single-ticker strategies only")
        ticker = tickers[0]
        simulation_scale = normalize_scale(_SIMULATION_SCALE)
        base_scale = normalize_scale(base_scale)
        if not is_finer_or_equal(simulation_scale, base_scale):
            raise ValueError(f"simulation_scale {simulation_scale!r} must be <= base_scale {base_scale!r}")
        if not scale_divides(simulation_scale, base_scale):
            raise ValueError(f"simulation_scale {simulation_scale!r} must divide base_scale {base_scale!r}")

        subs = _subscription_specs_for_live_bars(startup)

        with SessionLocal() as session:
            upsert_runner_subscriptions(session, run_id=run_id, runner_id=runner_id, subs=subs)
            prune_stale_subscriptions(session, max_age_seconds=float(args.subs_ttl_s))
            _emit_db_event(
                session,
                run_id=run_id,
                kind="startup",
                unixtime=int(time.time()),
                payload={"startup": json.loads(startup.model_dump_json())},
            )
            _emit_db_event(
                session,
                run_id=run_id,
                kind="status",
                unixtime=int(time.time()),
                payload={"status": "running", "ticker": ticker, "base_scale": base_scale},
            )
            session.commit()

        if enable_trading:
            order_stream = _AlpacaOrderUpdateStream(run_id=run_id, paper=bool(args.paper))
            order_stream.start()

        driver_rows: list[dict] = []
        driver_index: list[pd.Timestamp] = []
        driver_df = pd.DataFrame()
        base_df = pd.DataFrame()

        ticker_subs, indicator_subs, renko_subs = compile_subscriptions(
            startup, base_scale, simulation_scale
        )
        indicator_models = [s.source for s in indicator_subs]
        engine = IndicatorEngine(indicator_models)

        bucket_to_row: dict[pd.Timestamp, int] = {}
        cached_base_len = 0
        running: RunningBar | None = None
        cur_base_idx: int | None = None
        renko_states: list[RenkoState] = [RenkoState() for _ in renko_subs]
        j = 0
        stop_poll_s = max(0.25, float(args.stop_poll_s))
        last_stop_mon = time.monotonic() - stop_poll_s
        order_reconcile_s = max(1.0, float(args.order_reconcile_s))
        last_order_reconcile_mon = time.monotonic() - order_reconcile_s

        def process_market_events(session, events: list[LiveRunEvent], *, emit_outputs: bool) -> bool:
            nonlocal driver_df, base_df, cached_base_len, j, running, cur_base_idx
            appended = False
            for ev in events:
                payload = ev.payload or {}
                sym = (payload.get("symbol") or payload.get("ticker") or "").strip().upper()
                if sym != ticker:
                    continue
                ts_raw = payload.get("t")
                ts = pd.Timestamp(ts_raw) if ts_raw else pd.Timestamp(int(ev.unixtime or time.time()), unit="s")
                if getattr(ts, "tzinfo", None) is None and getattr(ts, "tz", None) is None:
                    ts = ts.tz_localize("UTC")
                else:
                    ts = ts.tz_convert("UTC")
                o = float(payload.get("open") or payload.get("o") or 0.0)
                h = float(payload.get("high") or payload.get("h") or 0.0)
                l = float(payload.get("low") or payload.get("l") or 0.0)
                c = float(payload.get("close") or payload.get("c") or 0.0)
                v = float(payload.get("volume") or payload.get("v") or 0.0)
                driver_index.append(ts)
                driver_rows.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
                appended = True

            if not appended:
                return False

            driver_df = pd.DataFrame(driver_rows, index=pd.DatetimeIndex(driver_index))
            base_df = aggregate_to_base(driver_df, base_scale)
            if base_df.empty:
                return False
            engine.fit(base_df)

            if len(base_df.index) != cached_base_len:
                bucket_to_row.clear()
                for i in range(len(base_df.index)):
                    b = floor_ts_to_scale(pd.Timestamp(base_df.index[i]), base_scale)
                    bucket_to_row[b] = i
                cached_base_len = len(base_df.index)

            wrote_any = False
            while j < len(driver_df):
                step, running, cur_base_idx = _step_from_driver_index(
                    driver_df=driver_df,
                    base_df=base_df,
                    base_scale=base_scale,
                    simulation_scale=simulation_scale,
                    j=j,
                    bucket_to_row=bucket_to_row,
                    running=running,
                    cur_base_idx=cur_base_idx,
                    ticker_subs=ticker_subs,
                    indicator_subs=indicator_subs,
                    renko_subs=renko_subs,
                    indicator_engine=engine,
                    renko_states=renko_states,
                )
                j += 1
                if step is None or not step.fired or not emit_outputs:
                    continue

                for line in expand_step_to_lines(
                    step,
                    portfolio_provider=lambda: _portfolio_snapshot_from_alpaca(
                        client,
                        strategy_tickers=tickers,
                    ),
                ):
                    _emit_db_event(
                        session,
                        run_id=run_id,
                        kind="input",
                        unixtime=int(line.unixtime),
                        payload={"input": json.loads(line.model_dump_json())},
                    )
                    for pt in line.points:
                        if pt.kind == "ohlc":
                            _emit_db_event(
                                session,
                                run_id=run_id,
                                kind="bar",
                                unixtime=int(line.unixtime),
                                payload=pt.model_dump(mode="json"),
                            )
                        elif pt.kind == "indicator":
                            _emit_db_event(
                                session,
                                run_id=run_id,
                                kind="indicator_in",
                                unixtime=int(line.unixtime),
                                payload=pt.model_dump(mode="json"),
                            )
                        elif pt.kind == "portfolio":
                            _emit_db_event(
                                session,
                                run_id=run_id,
                                kind="portfolio",
                                unixtime=int(line.unixtime),
                                payload=pt.model_dump(mode="json"),
                            )
                        elif pt.kind == "renko":
                            _emit_db_event(
                                session,
                                run_id=run_id,
                                kind="renko",
                                unixtime=int(line.unixtime),
                                payload=pt.model_dump(mode="json"),
                            )

                    _log_strategy_input(line)
                    out = rt.send(line)
                    _log_strategy_output(out)
                    _emit_db_event(
                        session,
                        run_id=run_id,
                        kind="output",
                        unixtime=int(line.unixtime),
                        payload={"output": json.loads(out.model_dump_json())},
                    )

                    for item in out.root:
                        if isinstance(item, OutputMarketTradeOrder):
                            id_meta = _maybe_execute_market_order(
                                client,
                                session=session,
                                run_id=run_id,
                                unixtime=int(line.unixtime),
                                order=item,
                                enable_trading=enable_trading,
                            )
                            os_payload = dict(item.model_dump(mode="json"))
                            if id_meta is not None:
                                os_payload.update(id_meta)
                                _add_rejection_comment(os_payload)
                            _emit_db_event(
                                session,
                                run_id=run_id,
                                kind="order_signal",
                                unixtime=int(line.unixtime),
                                payload=os_payload,
                            )
                        elif isinstance(item, OutputIndicatorDataPoint):
                            _emit_db_event(
                                session,
                                run_id=run_id,
                                kind="indicator_out",
                                unixtime=int(line.unixtime),
                                payload=item.model_dump(mode="json"),
                            )
                        elif isinstance(item, OutputChart):
                            _emit_db_event(
                                session,
                                run_id=run_id,
                                kind="chart",
                                unixtime=int(line.unixtime),
                                payload=item.model_dump(mode="json"),
                            )
                        elif isinstance(item, OutputTimeAck):
                            continue

                    wrote_any = True
            return wrote_any

        def replay_strategy_inputs() -> int:
            replayed = 0
            after = 0
            while True:
                with SessionLocal() as session:
                    rows = read_run_strategy_inputs_after(
                        session,
                        run_id=run_id,
                        after_id=after,
                        limit=500,
                    )
                if not rows:
                    return replayed
                for ev in rows:
                    after = int(ev.id)
                    payload = ev.payload or {}
                    stored_input = payload.get("input")
                    if not isinstance(stored_input, dict):
                        continue
                    rt.send(StrategyInput.model_validate(stored_input))
                    replayed += 1

        with SessionLocal() as session:
            run = session.get(LiveRun, run_id)
            last_event_id = int(run.last_input_event_id or 0) if run is not None else 0

        if last_event_id > 0:
            after = 0
            while True:
                with SessionLocal() as session:
                    replay_events = read_run_market_events_after(
                        session,
                        run_id=run_id,
                        after_id=after,
                        limit=500,
                        through_id=last_event_id,
                    )
                    if replay_events:
                        process_market_events(session, replay_events, emit_outputs=False)
                if not replay_events:
                    break
                after = int(replay_events[-1].id)
            replayed = replay_strategy_inputs()
            logger.info("replayed %s strategy input event(s)", replayed)

        while True:
            if time.monotonic() - last_stop_mon >= stop_poll_s:
                last_stop_mon = time.monotonic()
                with SessionLocal() as session:
                    lr = session.get(LiveRun, run_id)
                    if live_run_row_requests_stop(lr):
                        logger.info("live run stop requested, exiting main loop")
                        break
            now = time.time()
            if now - last_touch >= touch_every_s:
                with SessionLocal() as session:
                    touch_runner_subscriptions(session, runner_id=runner_id)
                    prune_stale_subscriptions(session, max_age_seconds=float(args.subs_ttl_s))
                    session.commit()
                last_touch = now
            if enable_trading and time.monotonic() - last_order_reconcile_mon >= order_reconcile_s:
                last_order_reconcile_mon = time.monotonic()
                with SessionLocal() as session:
                    changed = _reconcile_live_run_orders(client, session=session, run_id=run_id)
                    if changed:
                        session.commit()

            with SessionLocal() as session:
                events = read_run_market_events_after(
                    session,
                    run_id=run_id,
                    after_id=last_event_id,
                    limit=500,
                )

            if not events:
                time.sleep(max(0.01, float(args.poll_ms) / 1000.0))
                continue

            last_event_id = int(events[-1].id)
            with SessionLocal() as session:
                process_market_events(session, events, emit_outputs=True)
                run = session.get(LiveRun, run_id)
                if run is not None:
                    run.last_input_event_id = int(last_event_id)
                    run.updated_at = datetime.now(timezone.utc)
                    session.add(run)
                session.commit()

    except KeyboardInterrupt:
        logger.info("received KeyboardInterrupt, shutting down")
        return 0
    finally:
        try:
            if order_stream is not None:
                order_stream.stop()
        except Exception:
            logger.exception("failed to stop order update stream")
        try:
            with SessionLocal() as session:
                run = session.get(LiveRun, run_id)
                if run is not None:
                    run.status = "stopped"
                    run.status_text = ""
                    run.updated_at = datetime.now(timezone.utc)
                    session.add(run)
                    _emit_db_event(
                        session,
                        run_id=run_id,
                        kind="status",
                        unixtime=int(time.time()),
                        payload={"status": "stopped"},
                    )
                delete_runner_subscriptions(session, runner_id=runner_id)
                session.commit()
        except Exception:
            logger.exception("failed to delete runner subscriptions")
        try:
            rt.close()
        except Exception:
            pass
        if temp_workspace is not None:
            shutil.rmtree(temp_workspace, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

