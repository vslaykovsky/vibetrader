from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)
_MARKET_SESSIONS = {"regular", "extended", "all"}

from application.queries.historical_bars import HistoricalBarsQuery
from application.schemas.simulation_dto import InitSimulationCommand, simulation_event
from application.services.simulation_limits import (
    min_calendar_end_covering_bar_count,
    read_strategy_max_leverage,
    read_strategy_scale,
)
from application.services.indicators import IndicatorEngine
from application.services.portfolio import Portfolio
from application.services.scale_utils import (
    is_finer_or_equal,
    normalize_scale,
    scale_divides,
    scale_minutes,
)
from application.services.simulation_driver import (
    aggregate_to_base,
    assign_subscription_ids,
    compile_subscriptions,
    expand_step_to_lines,
    iter_simulation_steps,
)
from application.services.simulation_registry import SimulationRegistry
from application.services.simulation_session import SimulationSession
from application.services.strategy_runtime import StrategyRuntime, StrategyRuntimeError
from strategies_v2.utils import (
    InputOhlcDataPoint,
    InputTrainedModelParams,
    Ohlc,
    OutputIndicatorSeriesCatalog,
    OutputIndicatorSubscriptionOrder,
    OutputMarketTradeOrder,
    OutputTickerSubscription,
    OutputTrainedModelParams,
    StrategyInput,
    StrategyOutput,
)


def _trade_event_payload(trade: Any, unixtime: int) -> dict[str, Any]:
    return simulation_event(
        "trade",
        unixtime=unixtime,
        ticker=trade.ticker,
        direction=trade.direction,
        action=trade.action,
        label=trade.label,
        price=trade.price,
        deposit_ratio=trade.deposit_ratio,
        position_before_order=trade.position_before_order,
        position_after_order_filled=trade.position_after_order_filled,
        reason=trade.reason or "strategy",
        valid=trade.valid,
    )


def _padding_days_for_indicator_subscriptions(subs: list[Any]) -> int:
    max_bars = 5
    for s in subs:
        k = getattr(s, "kind", None)
        if k == "sma":
            max_bars = max(max_bars, int(s.period) * 3)
        elif k == "ema":
            max_bars = max(max_bars, int(s.period) * 3)
        elif k == "macd":
            max_bars = max(max_bars, (int(s.slow_period) + int(s.signal_period)) * 3)
        elif k == "rsi":
            max_bars = max(max_bars, int(s.period) * 3)
        elif k == "atr":
            max_bars = max(max_bars, int(s.period) * 3)
        elif k == "renko" and getattr(s, "brick_size_mode", "fixed") == "atr":
            max_bars = max(max_bars, int(s.atr_period) * 3)
        elif k == "bb":
            max_bars = max(max_bars, int(s.period) * 3)
        elif k == "stochastic":
            max_bars = max(
                max_bars,
                (int(s.k_period) + int(s.k_slowing) + int(s.d_period)) * 3,
            )
        elif k == "fibonacci":
            max_bars = max(max_bars, int(s.lookback) * 3)
    return max(30, min(500, max_bars))


def _trained_model_params_input_from_workspace(workspace: Path) -> InputTrainedModelParams | None:
    path = workspace / "trained_model_params.json"
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8")
    try:
        out = OutputTrainedModelParams.model_validate_json(raw)
        return InputTrainedModelParams(name=out.name, data=out.data)
    except Exception:
        return InputTrainedModelParams.model_validate_json(raw)


def _ticker_and_scale_from_startup(startup: StrategyOutput) -> tuple[str, str]:
    for p in startup.root:
        if isinstance(p, OutputTickerSubscription):
            return p.ticker, p.scale
    raise ValueError("Strategy startup did not include ticker_subscription")


def _subscribed_tickers_and_base_scale(startup: StrategyOutput) -> tuple[list[str], str]:
    rows: list[tuple[str, str]] = []
    for p in startup.root:
        if isinstance(p, OutputTickerSubscription):
            rows.append((p.ticker.strip(), normalize_scale(p.scale)))
    if not rows:
        for spec in _indicator_subscriptions_from_startup(startup):
            t = getattr(spec, "ticker", None)
            s = getattr(spec, "scale", None)
            if isinstance(t, str) and t.strip() and isinstance(s, str) and s.strip():
                rows.append((t.strip(), normalize_scale(s)))
    if not rows:
        raise ValueError("Strategy startup did not include ticker_subscription")
    scales = {s for _, s in rows}
    if len(scales) != 1:
        raise ValueError(
            "All ticker_subscription entries must use the same scale; "
            f"got {[(t, s) for t, s in rows]}"
        )
    return list(dict.fromkeys(t for t, _ in rows)), next(iter(scales))


def _subscription_session(spec: Any) -> str:
    value = str(getattr(spec, "session", "all") or "all").strip().lower()
    if value not in _MARKET_SESSIONS:
        raise ValueError("session must be one of: regular, extended, all")
    return value


def _subscribed_ticker_sessions(startup: StrategyOutput) -> dict[str, str]:
    rows: list[tuple[str, str]] = []
    for p in startup.root:
        if isinstance(p, OutputTickerSubscription):
            rows.append((p.ticker.strip(), _subscription_session(p)))
    for spec in _indicator_subscriptions_from_startup(startup):
        t = getattr(spec, "ticker", None)
        if isinstance(t, str) and t.strip():
            rows.append((t.strip(), _subscription_session(spec)))
    out: dict[str, str] = {}
    for ticker, session in rows:
        prev = out.get(ticker)
        if prev is not None and prev != session:
            raise ValueError(
                f"All subscriptions for ticker {ticker!r} must use the same session; got {prev!r} and {session!r}"
            )
        out[ticker] = session
    return out


def _indicator_subscriptions_from_startup(startup: StrategyOutput) -> list[Any]:
    out: list[Any] = []
    for p in startup.root:
        if isinstance(p, OutputIndicatorSubscriptionOrder):
            out.append(p.indicator)
    return out


def _row_unixtime(ts: Any) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return int(t.timestamp())


# Keep at least this many base-scale bars **ahead** of the chart display anchor (see session).
LOOKAHEAD_BASE_BARS = 100


def _base_row_through_chart_anchor(
    base_df: pd.DataFrame,
    base_scale: str,
    chart_scale: str,
    anchor_chart_bar_open_unix: int,
) -> int:
    """Largest base-row index whose bar interval ends on or before the chart bucket end (UTC)."""
    chart_scale_n = normalize_scale(chart_scale)
    base_scale_n = normalize_scale(base_scale)
    chart_end = pd.Timestamp(anchor_chart_bar_open_unix, unit="s", tz="UTC") + pd.Timedelta(
        minutes=scale_minutes(chart_scale_n)
    )
    base_td = pd.Timedelta(minutes=scale_minutes(base_scale_n))
    threshold = chart_end - base_td
    if base_df.empty:
        return -1
    idx = base_df.index
    t = threshold
    if idx.tz is None:
        if t.tzinfo is not None:
            t = t.tz_convert("UTC").tz_localize(None)
    else:
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        else:
            t = t.tz_convert("UTC")
    pos = int(idx.searchsorted(t, side="right")) - 1
    return max(-1, pos)


def _sim_start_base_row(base_df: pd.DataFrame, start: date) -> int:
    for i, ts in enumerate(base_df.index):
        if pd.Timestamp(ts).date() >= start:
            return i
    raise ValueError("No bars in requested simulation date range (after padding)")


def _simulation_row_range(base_df: pd.DataFrame, start: date, end: date) -> tuple[int, int]:
    """
    Return inclusive [start_row, end_row] indices in ``base_df`` that fall within
    the date window [start, end] (UTC-normalized). Raises if no rows match.
    """
    if base_df.empty:
        raise ValueError("No bars available")

    idx = base_df.index
    start_ts = pd.Timestamp(start).tz_localize("UTC")
    end_excl = pd.Timestamp(end).tz_localize("UTC") + pd.Timedelta(days=1)

    if getattr(idx, "tz", None) is None:
        # Naive index: compare using naive UTC timestamps.
        start_cmp = start_ts.tz_localize(None)
        end_cmp = end_excl.tz_localize(None)
    else:
        start_cmp = start_ts
        end_cmp = end_excl

    start_i = int(idx.searchsorted(start_cmp, side="left"))
    end_i = int(idx.searchsorted(end_cmp, side="left")) - 1
    if start_i < 0:
        start_i = 0
    if end_i > (len(idx) - 1):
        end_i = len(idx) - 1
    if start_i > end_i:
        raise ValueError("No bars in requested simulation date range")
    return start_i, end_i


def _ensure_loaded_through_abs_base_row(
    *,
    bars_query: HistoricalBarsQuery,
    driver_holder: dict[str, pd.DataFrame],
    base_holder: dict[str, pd.DataFrame],
    engine: IndicatorEngine,
    ticker: str,
    session: str,
    dividend_adjusted: bool,
    sim_scale: str,
    base_scale: str,
    scale_for_fetch: str,
    need_abs_row: int,
) -> None:
    """Extend ``driver_holder`` / ``base_holder`` until ``base`` covers ``need_abs_row`` or history ends.

    Safeguards:
    - stops once ``start_next`` would request data beyond *yesterday* (provider cap);
    - stops if the rightmost index of ``driver_df`` does not advance after a fetch
      (defends against providers that return rows inside the existing window or
      duplicate the last bar without producing newer ones).
    """
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    while len(base_holder["df"].index) - 1 < need_abs_row:
        driver_df = driver_holder["df"]
        prev_rows = len(driver_df.index)
        prev_last_ts = pd.Timestamp(driver_df.index[-1])
        last_d = prev_last_ts.date()
        start_next = last_d + timedelta(days=1)
        if start_next > yesterday:
            return
        extra_end = min_calendar_end_covering_bar_count(
            start_next,
            scale_for_fetch,
            LOOKAHEAD_BASE_BARS + 40,
        )
        part, _ = bars_query.fetch_chunked_merge(
            ticker,
            sim_scale,
            start_next,
            extra_end,
            padding_days=0,
            provider=None,
            session=session,
            dividend_adjusted=dividend_adjusted,
        )
        if part.empty:
            return
        merged = pd.concat([driver_df, part]).sort_index()
        merged = merged[~merged.index.duplicated(keep="first")]
        if len(merged.index) <= prev_rows:
            return
        new_last_ts = pd.Timestamp(merged.index[-1])
        if new_last_ts <= prev_last_ts:
            logger.warning(
                "_ensure_loaded_through_abs_base_row: rightmost bar did not advance "
                "(prev=%s new=%s rows %s→%s) — provider returned only in-window rows; stopping",
                prev_last_ts,
                new_last_ts,
                prev_rows,
                len(merged.index),
            )
            return
        driver_holder["df"] = merged
        new_base = (
            merged
            if sim_scale == base_scale
            else aggregate_to_base(merged, base_scale)
        )
        base_holder["df"] = new_base
        engine.fit(new_base)


def _indicator_series_catalog_payload(
    startup: StrategyOutput,
) -> list[dict[str, str]] | None:
    for p in startup.root:
        if isinstance(p, OutputIndicatorSeriesCatalog):
            return [e.model_dump(mode="json") for e in p.series]
    return None


def _read_simulation_scale(workspace: Path, base_scale: str) -> str:
    try:
        data = json.loads((workspace / "params.json").read_text(encoding="utf-8"))
        raw = data.get("simulation_scale")
        if isinstance(raw, str) and raw.strip():
            sim_scale = normalize_scale(raw)
            base = normalize_scale(base_scale)
            if not is_finer_or_equal(sim_scale, base):
                raise ValueError(
                    f"simulation_scale {sim_scale!r} must be at most as coarse as scale {base!r}"
                )
            if not scale_divides(sim_scale, base):
                raise ValueError(
                    f"simulation_scale {sim_scale!r} must divide scale {base!r}"
                )
            return sim_scale
    except ValueError:
        raise
    except Exception:
        pass
    return normalize_scale(base_scale)


class StrategySimulateCommandHandler:
    def __init__(
        self,
        registry: SimulationRegistry,
        bars_query: HistoricalBarsQuery,
        *,
        strategy_v2_workspace_parent: Path | None = None,
        strategy_entry_script: str = "strategy.py",
    ) -> None:
        self._registry = registry
        self._bars = bars_query
        self._strategy_v2_workspace_parent = strategy_v2_workspace_parent
        self._strategy_entry_script = strategy_entry_script

    def _resolve_strategy_workspace(self, cmd: InitSimulationCommand) -> Path:
        root = self._strategy_v2_workspace_parent or (
            Path(__file__).resolve().parents[2] / "strategies_v2"
        )
        candidate = root / cmd.thread_id
        if candidate.is_dir():
            return candidate
        if self._strategy_v2_workspace_parent is not None:
            return root
        raise ValueError(f"strategy workspace not found: {candidate}")

    def pause(self, user_id: str, thread_id: str) -> None:
        sess = self._registry.get(user_id, thread_id)
        if sess is not None:
            sess.pause.clear()
            sess.emit(simulation_event("status", status="paused"))

    def play(self, user_id: str, thread_id: str) -> None:
        sess = self._registry.get(user_id, thread_id)
        if sess is None:
            return
        if sess.is_worker_alive():
            sess.pause.set()
            sess.emit(simulation_event("status", status="running"))
            return
        cmd = sess.pending_cmd
        if cmd is None:
            sess.emit(
                simulation_event(
                    "status",
                    status="error",
                    message="call POST /simulation/init before play",
                )
            )
            return
        workspace = self._resolve_strategy_workspace(cmd)

        def run() -> None:
            self._run_simulation_worker(sess, cmd, workspace)

        sess.begin_run(run)

    def change_speed(self, user_id: str, thread_id: str, bps: float) -> None:
        if bps <= 0:
            raise ValueError("bps must be positive")
        sess = self._registry.get(user_id, thread_id)
        if sess is not None:
            sess.set_speed_bps(bps)
            sess.emit(simulation_event("speed", bps=float(bps)))

    def notify_display_anchor(
        self,
        user_id: str,
        thread_id: str,
        *,
        chart_last_bar_unixtime: int,
        chart_scale: str,
    ) -> None:
        """Called from ``GET /simulation/display_bars`` while the worker runs (chart playback cursor)."""
        sess = self._registry.get(user_id, thread_id)
        if sess is None or not sess.is_worker_alive():
            return
        sess.set_display_anchor(chart_last_bar_unixtime, chart_scale)

    def stop(self, user_id: str, thread_id: str) -> None:
        self._registry.remove(user_id, thread_id)

    def init(self, cmd: InitSimulationCommand) -> str:
        workspace = self._resolve_strategy_workspace(cmd)
        strategy_scale = read_strategy_scale(workspace / "params.json")
        session = SimulationSession(
            user_id=cmd.user_id,
            thread_id=cmd.thread_id,
            initial_speed_bps=cmd.initial_speed_bps,
            pending_cmd=cmd,
        )
        self._registry.replace(cmd.user_id, cmd.thread_id, session)
        session.emit(
            simulation_event("status", status="ready", strategy_scale=strategy_scale)
        )
        return strategy_scale

    def _run_multi_ticker_simulation_worker(
        self,
        *,
        sess: SimulationSession,
        cmd: InitSimulationCommand,
        workspace: Path,
        rt: StrategyRuntime,
        startup: StrategyOutput,
        tickers: list[str],
        base_scale: str,
        sim_scale: str,
        ind_specs: list[Any],
        trained_model_input: InputTrainedModelParams | None,
    ) -> None:
        if sim_scale != base_scale:
            raise ValueError(
                f"multi-ticker simulation requires simulation_scale ({sim_scale!r}) == scale ({base_scale!r})"
            )
        if any(getattr(s, "kind", None) == "renko" for s in ind_specs):
            raise ValueError("multi-ticker simulation does not support renko subscriptions yet")
        ticker_set = set(tickers)
        for spec in ind_specs:
            st = getattr(spec, "ticker", None)
            if isinstance(st, str) and st.strip() and st.strip() not in ticker_set:
                raise ValueError(
                    f"Indicator subscription ticker {st!r} not in subscribed tickers {tickers!r}"
                )
        for p in startup.root:
            if isinstance(p, OutputTickerSubscription) and p.partial:
                raise ValueError(
                    f"multi-ticker simulation does not support partial ticker_subscription (ticker={p.ticker!r})"
                )
            if isinstance(p, OutputIndicatorSubscriptionOrder) and getattr(
                p.indicator, "partial", False
            ):
                raise ValueError(
                    "multi-ticker simulation does not support partial indicator subscriptions"
                )

        padding = _padding_days_for_indicator_subscriptions(ind_specs)
        scale_for_fetch = read_strategy_scale(workspace / "params.json")
        fetch_end = min_calendar_end_covering_bar_count(
            cmd.start_date,
            scale_for_fetch,
            LOOKAHEAD_BASE_BARS + 50,
        )
        per_base_df: dict[str, pd.DataFrame] = {}
        per_engine: dict[str, IndicatorEngine] = {}
        per_engine_ind_subs: dict[str, list] = {}
        ticker_sessions = _subscribed_ticker_sessions(startup)
        for t in tickers:
            driver_df_t, _ = self._bars.fetch_chunked_merge(
                t,
                sim_scale,
                cmd.start_date,
                fetch_end,
                padding_days=padding,
                provider=None,
                session=ticker_sessions.get(t, "all"),
                dividend_adjusted=cmd.adjust_for_dividends,
            )
            if driver_df_t.empty:
                logger.warning("simulation got empty OHLC for ticker=%s", t)
                continue
            base_df_t = driver_df_t if sim_scale == base_scale else aggregate_to_base(
                driver_df_t, base_scale
            )
            if base_df_t.empty:
                continue
            per_base_df[t] = base_df_t
            ind_for_t = [
                s
                for s in ind_specs
                if getattr(s, "ticker", None) == t
                and getattr(s, "kind", None) != "renko"
            ]
            per_engine_ind_subs[t] = ind_for_t
            engine = IndicatorEngine(ind_for_t)
            engine.fit(base_df_t)
            per_engine[t] = engine
        if not per_base_df:
            sess.emit(
                simulation_event(
                    "status",
                    status="done",
                    message=(
                        "Provider returned no OHLC for the requested range — "
                        "try an earlier start date or a different ticker."
                    ),
                )
            )
            return

        primary_ticker = tickers[0] if tickers[0] in per_base_df else next(iter(per_base_df))
        primary_base_df = per_base_df[primary_ticker]
        try:
            sim_start_i = _sim_start_base_row(primary_base_df, cmd.start_date)
        except ValueError:
            sess.emit(
                simulation_event(
                    "status",
                    status="done",
                    message=(
                        "No market bars available at or after the chosen "
                        "start date — try an earlier start."
                    ),
                )
            )
            return
        sim_start_unix = int(pd.Timestamp(cmd.start_date).tz_localize("UTC").timestamp())

        ticker_sub_order: list[OutputTickerSubscription] = [
            p for p in startup.root if isinstance(p, OutputTickerSubscription)
        ]
        indicator_sub_order = [
            p.indicator for p in startup.root if isinstance(p, OutputIndicatorSubscriptionOrder)
        ]
        ts_to_row: dict[str, dict[pd.Timestamp, int]] = {}
        for t, df in per_base_df.items():
            row_map: dict[pd.Timestamp, int] = {}
            for i, ts in enumerate(df.index):
                ts_utc = pd.Timestamp(ts)
                if ts_utc.tzinfo is None:
                    ts_utc = ts_utc.tz_localize("UTC")
                row_map[ts_utc] = i
            ts_to_row[t] = row_map
        all_ts_sorted = sorted({ts for row_map in ts_to_row.values() for ts in row_map})
        primary_row_map = ts_to_row[primary_ticker]
        max_leverage = read_strategy_max_leverage(workspace / "params.json")
        portfolio = Portfolio(
            initial_deposit=cmd.initial_deposit,
            ticker=primary_ticker,
            max_leverage=max_leverage,
        )
        last_prices: dict[str, float] = {}
        pending_initial_points = [trained_model_input] if trained_model_input is not None else []

        sess.emit(simulation_event("status", status="running", strategy_scale=base_scale))
        sess.emit(simulation_event("speed", bps=float(cmd.initial_speed_bps)))
        catalog_rows = _indicator_series_catalog_payload(startup)
        if catalog_rows:
            sess.emit(simulation_event("indicator_series_catalog", series=catalog_rows))

        def _emit_cap_base_row() -> int:
            anchor_u, anchor_sc = sess.get_display_anchor()
            if anchor_u <= 0 or not anchor_sc:
                return sim_start_i - 1 + LOOKAHEAD_BASE_BARS
            return (
                _base_row_through_chart_anchor(
                    primary_base_df, base_scale, anchor_sc, anchor_u
                )
                + LOOKAHEAD_BASE_BARS
            )

        for ts in all_ts_sorted:
            base_unix = int(ts.timestamp())
            in_active_window = base_unix >= sim_start_unix
            if sess.stop_requested:
                sess.emit(simulation_event("status", status="stopped"))
                return
            while not sess.pause.is_set():
                if sess.stop_requested:
                    sess.emit(simulation_event("status", status="stopped"))
                    return
                sess.pause.wait(timeout=0.05)

            primary_row = primary_row_map.get(ts)
            if primary_row is not None and not sess.wait_until_base_row_allowed(
                _emit_cap_base_row, int(primary_row)
            ):
                sess.emit(simulation_event("status", status="stopped"))
                return

            step_points: list = []
            fills: dict[str, float] = {}
            primary_bar: tuple[float, float, float, float, float] | None = None
            for sub in ticker_sub_order:
                row_map = ts_to_row.get(sub.ticker)
                if row_map is None:
                    continue
                row = row_map.get(ts)
                if row is None:
                    continue
                df = per_base_df[sub.ticker]
                o = float(df.iloc[row]["open"])
                h = float(df.iloc[row]["high"])
                l = float(df.iloc[row]["low"])
                c = float(df.iloc[row]["close"])
                v = float(df.iloc[row]["volume"]) if "volume" in df.columns else 0.0
                step_points.append(
                    InputOhlcDataPoint(
                        id=str(sub.id),
                        ticker=sub.ticker,
                        ohlc=Ohlc(open=o, high=h, low=l, close=c, volume=v),
                        closed=True,
                    )
                )
                fills[sub.ticker] = c
                last_prices[sub.ticker] = c
                if sub.ticker == primary_ticker:
                    primary_bar = (o, h, l, c, v)

            for ind_sub in indicator_sub_order:
                t_ind = getattr(ind_sub, "ticker", None)
                row_map = ts_to_row.get(t_ind) if t_ind else None
                if row_map is None:
                    continue
                row = row_map.get(ts)
                if row is None:
                    continue
                eng = per_engine.get(t_ind)
                if eng is None:
                    continue
                local_subs = per_engine_ind_subs.get(t_ind, [])
                try:
                    local_idx = local_subs.index(ind_sub)
                except ValueError:
                    continue
                for pt in eng.values_at_row_for_subscription(local_idx, row):
                    step_points.append(pt.model_copy(update={"id": str(ind_sub.id)}))

            if step_points and in_active_window:
                portfolio.equity(last_prices)
                points = [
                    portfolio.to_portfolio_datapoint(),
                    *pending_initial_points,
                    *step_points,
                ]
                pending_initial_points = []
                out = rt.send(
                    StrategyInput(
                        unixtime=base_unix,
                        points=points,
                    )
                )
                orders = [
                    item for item in out.root if isinstance(item, OutputMarketTradeOrder)
                ]
                if orders:
                    first_trade = len(portfolio.trades)
                    portfolio.apply_market_orders(
                        orders,
                        prices=fills,
                        unixtime=base_unix,
                        reason="strategy",
                    )
                    for trade in portfolio.trades[first_trade:]:
                        sess.emit(_trade_event_payload(trade, base_unix))

            if last_prices and in_active_window:
                portfolio.record_equity(base_unix, last_prices)

            if (
                primary_row is not None
                and primary_bar is not None
                and primary_row >= sim_start_i
            ):
                o, h, l, c, v = primary_bar
                eq = portfolio.equity(last_prices)
                sess.emit(
                    simulation_event(
                        "bar",
                        unixtime=base_unix,
                        scale=base_scale,
                        ticker=primary_ticker,
                        ohlc={
                            "open": o,
                            "high": h,
                            "low": l,
                            "close": c,
                            "volume": v,
                        },
                        closed=True,
                    )
                )
                sess.emit(
                    simulation_event(
                        "pnl",
                        unixtime=base_unix,
                        equity=eq,
                        pnl_pct=eq / portfolio.initial_deposit - 1.0,
                    )
                )
        sess.emit(simulation_event("status", status="done"))

    def _run_simulation_worker(
        self,
        sess: SimulationSession,
        cmd: InitSimulationCommand,
        workspace: Path,
    ) -> None:
        rt: StrategyRuntime | None = None
        try:
            rt = StrategyRuntime(workspace, entry_script=self._strategy_entry_script)
            trained_model_input = _trained_model_params_input_from_workspace(workspace)
            startup = rt.start()
            startup = assign_subscription_ids(startup)
            tickers, base_scale = _subscribed_tickers_and_base_scale(startup)
            ticker_sessions = _subscribed_ticker_sessions(startup)
            ticker = tickers[0]
            ticker_session = ticker_sessions.get(ticker, "all")
            base_scale = normalize_scale(base_scale)
            sess.emit(
                simulation_event("status", status="starting", strategy_scale=base_scale)
            )
            sim_scale = (
                normalize_scale(cmd.initial_scale)
                if cmd.initial_scale
                else _read_simulation_scale(workspace, base_scale)
            )
            if not is_finer_or_equal(sim_scale, base_scale):
                raise ValueError(
                    f"simulation_scale {sim_scale!r} must be at most as coarse as scale {base_scale!r}"
                )
            if not scale_divides(sim_scale, base_scale):
                raise ValueError(
                    f"simulation_scale {sim_scale!r} must divide scale {base_scale!r}"
                )
            ind_specs = _indicator_subscriptions_from_startup(startup)
            if len(tickers) > 1:
                self._run_multi_ticker_simulation_worker(
                    sess=sess,
                    cmd=cmd,
                    workspace=workspace,
                    rt=rt,
                    startup=startup,
                    tickers=tickers,
                    base_scale=base_scale,
                    sim_scale=sim_scale,
                    ind_specs=ind_specs,
                    trained_model_input=trained_model_input,
                )
                return
            padding = _padding_days_for_indicator_subscriptions(ind_specs)
            scale_for_fetch = read_strategy_scale(workspace / "params.json")
            fetch_end = min_calendar_end_covering_bar_count(
                cmd.start_date,
                scale_for_fetch,
                LOOKAHEAD_BASE_BARS + 50,
            )
            driver_df, _chunks = self._bars.fetch_chunked_merge(
                ticker,
                sim_scale,
                cmd.start_date,
                fetch_end,
                padding_days=padding,
                provider=None,
                session=ticker_session,
                dividend_adjusted=cmd.adjust_for_dividends,
            )
            if driver_df.empty:
                logger.info(
                    "simulation got empty OHLC for ticker=%s start_date=%s; finishing as done",
                    ticker,
                    cmd.start_date.isoformat(),
                )
                sess.emit(
                    simulation_event(
                        "status",
                        status="done",
                        message=(
                            "Provider returned no OHLC for the requested range — "
                            "try an earlier start date or a different ticker."
                        ),
                    )
                )
                return
            base_df = (
                driver_df
                if sim_scale == base_scale
                else aggregate_to_base(driver_df, base_scale)
            )
            if base_df.empty:
                logger.info(
                    "simulation got empty base_df after aggregation start_date=%s",
                    cmd.start_date.isoformat(),
                )
                sess.emit(
                    simulation_event(
                        "status",
                        status="done",
                        message="No base-scale bars after aggregation.",
                    )
                )
                return
            try:
                sim_start_i = _sim_start_base_row(base_df, cmd.start_date)
            except ValueError:
                # No bars at/after ``start_date`` — provider has nothing fresher than
                # the chosen anchor. Finish the run gracefully so the chart can still
                # display historical OHLC; the worker simply has no trades to emit.
                logger.info(
                    "simulation has no bars at/after start_date=%s; finishing as done",
                    cmd.start_date.isoformat(),
                )
                sess.emit(
                    simulation_event(
                        "status",
                        status="done",
                        message=(
                            "No market bars available at or after the chosen "
                            "start date — try an earlier start."
                        ),
                    )
                )
                return
            # Wall-clock anchor: the absolute Unix start of ``cmd.start_date`` (UTC).
            # Orders / equity emitted at ``unixtime < sim_start_unix`` belong to the
            # warmup window and must never reach the chart.
            sim_start_unix = int(
                pd.Timestamp(cmd.start_date).tz_localize("UTC").timestamp()
            )
            logger.info(
                "simulation start anchor sim_start_i=%s sim_start_unix=%s start_date=%s",
                sim_start_i,
                sim_start_unix,
                cmd.start_date.isoformat(),
            )
            engine_subs = [s for s in ind_specs if getattr(s, "kind", None) != "renko"]
            engine = IndicatorEngine(engine_subs)
            engine.fit(base_df)
            driver_holder: dict[str, pd.DataFrame] = {"df": driver_df}
            base_holder: dict[str, pd.DataFrame] = {"df": base_df}
            _ensure_loaded_through_abs_base_row(
                bars_query=self._bars,
                driver_holder=driver_holder,
                base_holder=base_holder,
                engine=engine,
                ticker=ticker,
                session=ticker_session,
                dividend_adjusted=cmd.adjust_for_dividends,
                sim_scale=sim_scale,
                base_scale=base_scale,
                scale_for_fetch=scale_for_fetch,
                need_abs_row=sim_start_i + LOOKAHEAD_BASE_BARS,
            )
            ticker_subs, indicator_subs, renko_subs = compile_subscriptions(
                startup, base_scale, sim_scale
            )
            max_leverage = read_strategy_max_leverage(workspace / "params.json")
            portfolio = Portfolio(
                initial_deposit=cmd.initial_deposit,
                ticker=ticker,
                max_leverage=max_leverage,
            )
            pending_initial_points = [trained_model_input] if trained_model_input is not None else []
            sess.emit(
                simulation_event(
                    "status", status="running", strategy_scale=base_scale
                )
            )
            sess.emit(simulation_event("speed", bps=float(cmd.initial_speed_bps)))
            catalog_rows = _indicator_series_catalog_payload(startup)
            if catalog_rows:
                sess.emit(
                    simulation_event(
                        "indicator_series_catalog",
                        series=catalog_rows,
                    )
                )

            def _emit_cap_base_row() -> int:
                anchor_u, anchor_sc = sess.get_display_anchor()
                bdf = base_holder["df"]
                if anchor_u <= 0 or not anchor_sc:
                    a_last = sim_start_i - 1
                else:
                    a_last = _base_row_through_chart_anchor(
                        bdf, base_scale, anchor_sc, anchor_u
                    )
                return a_last + LOOKAHEAD_BASE_BARS

            for step in iter_simulation_steps(
                driver_df=driver_holder["df"],
                base_df=base_holder["df"],
                base_scale=base_scale,
                simulation_scale=sim_scale,
                ticker_subs=ticker_subs,
                indicator_subs=indicator_subs,
                indicator_engine=engine,
                renko_subs=renko_subs,
                driver_df_holder=driver_holder,
                base_df_holder=base_holder,
            ):
                if sess.stop_requested:
                    sess.emit(simulation_event("status", status="stopped"))
                    return
                while not sess.pause.is_set():
                    if sess.stop_requested:
                        sess.emit(simulation_event("status", status="stopped"))
                        return
                    sess.pause.wait(timeout=0.05)

                if not sess.wait_until_base_row_allowed(_emit_cap_base_row, int(step.base_row)):
                    sess.emit(simulation_event("status", status="stopped"))
                    return

                fill_price = step.running.close
                in_active_window = (
                    step.base_row >= sim_start_i
                    and int(step.unixtime) >= sim_start_unix
                )
                if step.fired and in_active_window:
                    for line in expand_step_to_lines(
                        step,
                        portfolio_provider=portfolio.to_portfolio_datapoint,
                    ):
                        if pending_initial_points:
                            line = line.model_copy(
                                update={
                                    "points": [
                                        line.points[0],
                                        *pending_initial_points,
                                        *line.points[1:],
                                    ]
                                }
                            )
                            pending_initial_points = []
                        out = rt.send(line)
                        for item in out.root:
                            if isinstance(item, OutputMarketTradeOrder):
                                first_trade = len(portfolio.trades)
                                portfolio.apply_market_order(
                                    ticker=item.ticker,
                                    direction=item.direction,
                                    deposit_ratio=item.deposit_ratio,
                                    price=fill_price,
                                    unixtime=line.unixtime,
                                    reason=item.short_explanation,
                                )
                                if len(portfolio.trades) == first_trade:
                                    continue
                                trade = portfolio.trades[-1]
                                sess.emit(_trade_event_payload(trade, line.unixtime))
                if in_active_window:
                    portfolio.record_equity(step.unixtime, fill_price)

                if step.is_base_close and step.base_row >= sim_start_i:
                    _ensure_loaded_through_abs_base_row(
                        bars_query=self._bars,
                        driver_holder=driver_holder,
                        base_holder=base_holder,
                        engine=engine,
                        ticker=ticker,
                        session=ticker_session,
                        dividend_adjusted=cmd.adjust_for_dividends,
                        sim_scale=sim_scale,
                        base_scale=base_scale,
                        scale_for_fetch=scale_for_fetch,
                        need_abs_row=step.base_row + LOOKAHEAD_BASE_BARS,
                    )
                    eq = portfolio.equity(fill_price)
                    sess.emit(
                        simulation_event(
                            "bar",
                            unixtime=int(pd.Timestamp(step.base_ts).timestamp()),
                            scale=base_scale,
                            ticker=ticker,
                            ohlc={
                                "open": float(step.running.open),
                                "high": float(step.running.high),
                                "low": float(step.running.low),
                                "close": float(step.running.close),
                                "volume": float(step.running.volume),
                            },
                            closed=True,
                        )
                    )
                    sess.emit(
                        simulation_event(
                            "pnl",
                            unixtime=step.unixtime,
                            equity=eq,
                            pnl_pct=eq / portfolio.initial_deposit - 1.0,
                        )
                    )
        except (StrategyRuntimeError, ValueError) as exc:
            logger.exception("simulation worker stopped with known error")
            sess.emit(simulation_event("status", status="error", message=str(exc)))
        except Exception as exc:
            logger.exception("simulation worker crashed unexpectedly")
            sess.emit(
                simulation_event(
                    "status",
                    status="error",
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            if rt is not None:
                rt.close()
