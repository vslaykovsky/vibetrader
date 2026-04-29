from __future__ import annotations

import argparse
import json
import logging
import os
import sys
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
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from sqlalchemy.exc import IntegrityError

from application.services.live_run_control import live_run_row_requests_stop
from application.services.alpaca_live_db import (
    LiveSubscriptionSpec,
    delete_runner_subscriptions,
    prune_stale_subscriptions,
    read_events_after_id,
    touch_runner_subscriptions,
    upsert_runner_subscriptions,
)
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
)
from application.services.scale_utils import floor_ts_to_scale
from application.services.strategy_runtime import StrategyRuntime
from db.models import LiveRun, LiveRunEvent, LiveRunOrder
from db.session import SessionLocal
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


def _portfolio_snapshot_from_alpaca(client: TradingClient) -> InputPortfolioDataPoint:
    positions = []
    for p in client.get_all_positions():
        sym = str(getattr(p, "symbol", "")).strip().upper()
        qty = float(getattr(p, "qty", 0.0) or 0.0)
        avg = float(getattr(p, "avg_entry_price", 0.0) or 0.0)
        if not sym or qty == 0:
            continue
        deposit_ratio = 1.0
        positions.append(
            {
                "ticker": sym,
                "order_type": "long" if qty > 0 else "short",
                "deposit_ratio": float(deposit_ratio),
                "volume_weighted_avg_entry_price": float(avg),
            }
        )
    return InputPortfolioDataPoint(positions=positions)


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
        brick_size = float(getattr(src, "brick_size", 0.0))
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


def _emit_db_event(session, *, run_id: str, kind: str, unixtime: int | None, payload: dict) -> None:
    session.add(
        LiveRunEvent(
            run_id=run_id,
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


def _maybe_execute_market_order(
    client: TradingClient,
    *,
    session,
    run_id: str,
    unixtime: int,
    order: OutputMarketTradeOrder,
    enable_trading: bool,
) -> dict[str, str] | None:
    if not enable_trading:
        return None
    sym = str(order.ticker).strip().upper()
    side = OrderSide.BUY if str(order.direction).lower() == "buy" else OrderSide.SELL
    dr = float(getattr(order, "deposit_ratio", 1.0) or 1.0)
    dr = max(0.0, min(1.0, dr))
    cid = _client_order_id_for_signal(run_id, int(unixtime), order)
    try:
        with session.begin_nested():
            row = LiveRunOrder(run_id=run_id, client_order_id=cid)
            session.add(row)
            session.flush()
            if side == OrderSide.BUY:
                acct = client.get_account()
                cash = float(getattr(acct, "cash", 0.0) or 0.0)
                notional = round(cash * dr, 2)
                if notional <= 0:
                    return {"client_order_id": cid, "alpaca_order_id": ""}
                req = MarketOrderRequest(
                    symbol=sym,
                    notional=notional,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=cid,
                )
                placed = client.submit_order(req)
                aid = str(getattr(placed, "id", "") or "")
                row.alpaca_order_id = aid
                return {"client_order_id": cid, "alpaca_order_id": aid}
            pos = None
            for p in client.get_all_positions():
                if str(getattr(p, "symbol", "")).strip().upper() == sym:
                    pos = p
                    break
            if pos is None:
                return {"client_order_id": cid, "alpaca_order_id": ""}
            qty = float(getattr(pos, "qty", 0.0) or 0.0)
            sell_qty = abs(qty) * dr
            if sell_qty <= 0:
                return {"client_order_id": cid, "alpaca_order_id": ""}
            req = MarketOrderRequest(
                symbol=sym,
                qty=sell_qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                client_order_id=cid,
            )
            placed = client.submit_order(req)
            aid = str(getattr(placed, "id", "") or "")
            row.alpaca_order_id = aid
            return {"client_order_id": cid, "alpaca_order_id": aid}
    except IntegrityError:
        return {"client_order_id": cid, "alpaca_order_id": ""}


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(
        description="Run a strategies_v2 strategy against live Alpaca events stored in DB, and optionally place trades via Alpaca Trading API."
    )
    parser.add_argument("--entry", required=True, help="Path to strategy entry script (e.g. strategy.py).")
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
    args = parser.parse_args(argv)

    entry_path = Path(args.entry).resolve()
    if not entry_path.is_file():
        parser.error(f"--entry must be an existing file: {entry_path}")
    workspace = entry_path.parent
    entry_script = entry_path.relative_to(workspace).as_posix()
    run_id = (str(args.run_id).strip() or str(uuid.uuid4())).strip()
    runner_id = (str(args.runner_id).strip() or run_id)[:64]

    client = _alpaca_client(paper=bool(args.paper))
    rt = StrategyRuntime(workspace, entry_script=entry_script)
    last_event_id = 0
    last_touch = 0.0
    touch_every_s = 10.0
    enable_trading = bool(args.enable_trading)

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
                    thread_id=workspace.name,
                    created_by=(str(args.created_by).strip() or None),
                    created_by_email=(str(args.created_by_email).strip() or None),
                    mode="paper" if bool(args.paper) else "live",
                    status="running",
                    status_text="starting",
                    entry_path=str(entry_path),
                    runner_backend=rb,
                    runner_id=runner_id,
                    last_input_event_id=0,
                )
                session.add(row)
            else:
                row.thread_id = workspace.name
                row.mode = "paper" if bool(args.paper) else "live"
                row.status = "running"
                row.status_text = "starting"
                row.entry_path = str(entry_path)
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

        initial_portfolio = _portfolio_snapshot_from_alpaca(client)
        initial_input = StrategyInput(
            unixtime=int(time.time()),
            points=[initial_portfolio],
        )
        _log_strategy_input(initial_input)
        startup = rt.start(initial_input=initial_input)
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
            upsert_runner_subscriptions(session, runner_id=runner_id, subs=subs)
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

            with SessionLocal() as session:
                events = read_events_after_id(session, after_id=last_event_id, limit=500)

            if not events:
                time.sleep(max(0.01, float(args.poll_ms) / 1000.0))
                continue

            wrote_any = False
            with SessionLocal() as session:
                for ev in events:
                    last_event_id = int(ev.id)
                    ch = (ev.channel or "").strip().lower()
                    payload = ev.payload or {}

                    if ch != "bars":
                        continue
                    sym = (ev.symbol or payload.get("symbol") or payload.get("ticker") or "").strip().upper()
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

                if not driver_rows:
                    session.commit()
                    continue

                driver_df = pd.DataFrame(driver_rows, index=pd.DatetimeIndex(driver_index))
                base_df = aggregate_to_base(driver_df, base_scale)
                if base_df.empty:
                    session.commit()
                    continue
                engine.fit(base_df)

                if len(base_df.index) != cached_base_len:
                    bucket_to_row.clear()
                    for i in range(len(base_df.index)):
                        b = floor_ts_to_scale(pd.Timestamp(base_df.index[i]), base_scale)
                        bucket_to_row[b] = i
                    cached_base_len = len(base_df.index)

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
                    if step is None or not step.fired:
                        continue

                    for line in expand_step_to_lines(
                        step,
                        portfolio_provider=lambda: _portfolio_snapshot_from_alpaca(client),
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
                                    os_payload["client_order_id"] = id_meta["client_order_id"]
                                    os_payload["alpaca_order_id"] = id_meta.get("alpaca_order_id", "")
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

                if wrote_any:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

