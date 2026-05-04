from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date
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

from application.queries.historical_bars import HistoricalBarsQuery
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
from application.services.simulation_limits import read_strategy_max_leverage
from application.services.strategy_runtime import StrategyRuntime, StrategyRuntimeError
from application.use_cases.strategy_simulate import (
    _indicator_subscriptions_from_startup,
    _padding_days_for_indicator_subscriptions,
    _read_simulation_scale,
    _simulation_row_range,
)
from application.services import backtest_data as backtest_utils
from strategies_v2.utils import (
    InputOhlcDataPoint,
    InputRenkoDataPoint,
    InputTrainedModelParams,
    Ohlc,
    OutputChart,
    OutputIndicatorDataPoint,
    OutputIndicatorSeriesCatalog,
    OutputIndicatorSubscriptionOrder,
    OutputMarketTradeOrder,
    OutputTickerSubscription,
    OutputTrainedModelParams,
    RenkoIndicatorSubscription,
    StrategyInput,
    StrategyOutput,
)

logger = logging.getLogger(__name__)

_DAILY_SCALES = {"1d", "1w"}

_INDICATOR_COLORS = [
    "#1e88e5",
    "#fb8c00",
    "#43a047",
    "#e53935",
    "#8e24aa",
    "#3949ab",
    "#00acc1",
    "#f4511e",
]


def _time_for_chart(unixtime: int, scale: str) -> str | int:
    if scale.lower() in _DAILY_SCALES:
        return pd.Timestamp(unixtime, unit="s", tz="UTC").strftime("%Y-%m-%d")
    return int(unixtime)


def _ticker_subscription_rows(startup: StrategyOutput) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for p in startup.root:
        if isinstance(p, OutputTickerSubscription):
            rows.append((p.ticker.strip(), normalize_scale(p.scale)))
    return rows


def _subscribed_tickers_and_base_scale(startup: StrategyOutput) -> tuple[list[str], str]:
    rows = _ticker_subscription_rows(startup)
    if not rows:
        ind_specs = _indicator_subscriptions_from_startup(startup)
        for spec in ind_specs:
            t = getattr(spec, "ticker", None)
            s = getattr(spec, "scale", None)
            if isinstance(t, str) and t.strip() and isinstance(s, str) and s.strip():
                rows.append((t.strip(), normalize_scale(s)))
    if not rows:
        raise ValueError(
            "Strategy startup did not include ticker_subscription or any indicator_subscription"
        )
    scales = {s for _, s in rows}
    if len(scales) != 1:
        raise ValueError(
            "All ticker_subscription entries must use the same scale; "
            f"got {[(t, s) for t, s in rows]}"
        )
    tickers = list(dict.fromkeys(t for t, _ in rows))
    return tickers, next(iter(scales))


def _max_drawdown(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            d = v / peak - 1.0
            if d < dd:
                dd = d
    return dd


def _periods_per_year(scale: str) -> float:
    s = normalize_scale(scale)
    if s == "1d":
        return 252.0
    if s == "1w":
        return 52.0
    trading_minutes_per_year = 252.0 * 6.5 * 60.0
    return trading_minutes_per_year / float(scale_minutes(s))


def _subscription_kind_is_price_overlay(kind: str) -> bool:
    return kind in ("sma", "ema", "bb", "fibonacci")


def _output_indicator_name_is_price_overlay(name: str) -> bool:
    n = (name or "").strip().lower()
    if n in ("sma", "ema"):
        return True
    if n.startswith("bb_") or n in ("bb_middle", "bb_upper", "bb_lower", "bb_mid"):
        return True
    if n.startswith("fib_"):
        return True
    return False


def _sharpe_ratio(equity: list[float], *, scale: str) -> float | None:
    if len(equity) < 2:
        return None
    rets: list[float] = []
    prev = float(equity[0])
    for cur_raw in equity[1:]:
        cur = float(cur_raw)
        if prev > 0:
            rets.append(cur / prev - 1.0)
        prev = cur
    if len(rets) < 2:
        return None
    mean = sum(rets) / float(len(rets))
    var = sum((r - mean) ** 2 for r in rets) / float(len(rets) - 1)
    if var <= 0:
        return None
    ann = _periods_per_year(scale) ** 0.5
    return ann * mean / (var**0.5)


def _trade_win_rate(trades: list) -> float | None:
    long_entries = []
    short_entries = []
    wins = 0
    closed = 0
    for trade in trades:
        if trade.action == "buy":
            long_entries.append(trade)
        elif trade.action == "sell" and long_entries:
            entry = long_entries.pop(0)
            wins += 1 if trade.price > entry.price else 0
            closed += 1
        elif trade.action == "sell_short":
            short_entries.append(trade)
        elif trade.action == "buy_to_cover" and short_entries:
            entry = short_entries.pop(0)
            wins += 1 if trade.price < entry.price else 0
            closed += 1
    return (wins / closed) if closed > 0 else None


def _build_position_value_chart(
    position_value_points: dict[str, list[backtest_utils.LwcTimeValuePoint]],
) -> backtest_utils.LightweightChartsChart | None:
    series = []
    for idx, ticker in enumerate(sorted(position_value_points)):
        points = position_value_points[ticker]
        if not points:
            continue
        series.append(
            backtest_utils.LwcTimeValueSeries(
                type="Line",
                label=f"{ticker} position value",
                options={
                    "color": _INDICATOR_COLORS[idx % len(_INDICATOR_COLORS)],
                    "lineWidth": 2,
                },
                data=points,
            )
        )
    if not series:
        return None
    return backtest_utils.LightweightChartsChart(
        title="Current position value",
        series=series,
    )


def _build_subscription_charts(
    *,
    tickers: list[str],
    base_scale: str,
    per_base_df: dict[str, pd.DataFrame],
    per_engine: dict[str, "IndicatorEngine"],
    per_engine_ind_subs: dict[str, list],
    primary_ticker: str,
    start_d: date,
    end_d: date,
    markers: list[backtest_utils.LwcMarker] | dict[str, list[backtest_utils.LwcMarker]],
    output_indicator_points: dict[str, list[tuple[int, float]]],
    renko_specs: list[RenkoIndicatorSubscription],
    renko_bricks: dict[str, list[tuple[int, float, float, str, float]]],
) -> list[backtest_utils.LightweightChartsChart]:
    win_start = pd.Timestamp(start_d, tz="UTC")
    win_end = pd.Timestamp(end_d, tz="UTC") + pd.Timedelta(days=1)
    charts: list[backtest_utils.LightweightChartsChart] = []
    for ticker in tickers:
        ticker_markers = markers.get(ticker, []) if isinstance(markers, dict) else markers
        df = per_base_df.get(ticker)
        if df is None or df.empty:
            continue
        ts_index = df.index
        if getattr(ts_index, "tzinfo", None) is None and getattr(ts_index, "tz", None) is None:
            ts_index = ts_index.tz_localize("UTC")
        visible_rows: list[int] = [
            i for i, ts in enumerate(ts_index) if win_start <= ts < win_end
        ]
        if not visible_rows:
            continue
        ticker_aux: list[backtest_utils.LightweightChartsChart] = []
        candles: list[backtest_utils.LwcCandlestickPoint] = []
        for r in visible_rows:
            base_unix = int(pd.Timestamp(ts_index[r]).timestamp())
            t_ax = _time_for_chart(base_unix, base_scale)
            candles.append(
                backtest_utils.LwcCandlestickPoint(
                    time=t_ax,
                    open=float(df.iloc[r]["open"]),
                    high=float(df.iloc[r]["high"]),
                    low=float(df.iloc[r]["low"]),
                    close=float(df.iloc[r]["close"]),
                )
            )
        price_series: list = [
            backtest_utils.LwcCandlestickSeries(
                label=ticker,
                options={"upColor": "#26a69a", "downColor": "#ef5350"},
                data=candles,
                markers=ticker_markers or None,
            )
        ]
        engine = per_engine.get(ticker)
        ind_subs = per_engine_ind_subs.get(ticker, [])
        color_idx = 0
        for local_idx, spec in enumerate(ind_subs):
            kind = getattr(spec, "kind", "indicator")
            by_name: dict[str, list[backtest_utils.LwcTimeValuePoint]] = {}
            for r in visible_rows:
                pts = (
                    engine.values_at_row_for_subscription(local_idx, r)
                    if engine is not None
                    else []
                )
                base_unix = int(pd.Timestamp(ts_index[r]).timestamp())
                t_ax = _time_for_chart(base_unix, base_scale)
                for pt in pts:
                    by_name.setdefault(pt.name, []).append(
                        backtest_utils.LwcTimeValuePoint(
                            time=t_ax, value=float(pt.value)
                        )
                    )
            spec_series: list = []
            for out_name, points in by_name.items():
                if not points:
                    continue
                period = getattr(spec, "period", None)
                base_label = f"{kind} {period}" if period is not None else kind
                label = f"{base_label} {out_name}" if out_name != kind else base_label
                color = _INDICATOR_COLORS[color_idx % len(_INDICATOR_COLORS)]
                color_idx += 1
                spec_series.append(
                    backtest_utils.LwcTimeValueSeries(
                        type="Line",
                        label=label,
                        options={"color": color, "lineWidth": 2},
                        data=points,
                    )
                )
            if not spec_series:
                continue
            if _subscription_kind_is_price_overlay(kind):
                price_series.extend(spec_series)
            else:
                period = getattr(spec, "period", None)
                sub_title = f"{kind} {period}" if period is not None else kind
                if ticker_markers:
                    spec_series[0] = spec_series[0].model_copy(update={"markers": ticker_markers})
                ticker_aux.append(
                    backtest_utils.LightweightChartsChart(
                        title=f"{ticker} {sub_title} ({base_scale})",
                        series=spec_series,
                    )
                )
        if ticker == primary_ticker:
            for out_name, pts in output_indicator_points.items():
                per_time: dict = {}
                for ut, val in pts:
                    ts_r = pd.Timestamp(int(ut), unit="s", tz="UTC")
                    if not (win_start <= ts_r < win_end):
                        continue
                    t_ax = _time_for_chart(int(ut), base_scale)
                    per_time[t_ax] = float(val)
                if not per_time:
                    continue
                data = [
                    backtest_utils.LwcTimeValuePoint(time=t, value=v)
                    for t, v in per_time.items()
                ]
                color = _INDICATOR_COLORS[color_idx % len(_INDICATOR_COLORS)]
                color_idx += 1
                line = backtest_utils.LwcTimeValueSeries(
                    type="Line",
                    label=f"output:{out_name}",
                    options={"color": color, "lineWidth": 2},
                    data=data,
                )
                if _output_indicator_name_is_price_overlay(out_name):
                    price_series.append(line)
                else:
                    if ticker_markers:
                        line = line.model_copy(update={"markers": ticker_markers})
                    ticker_aux.append(
                        backtest_utils.LightweightChartsChart(
                            title=f"{ticker} output:{out_name} ({base_scale})",
                            series=[line],
                        )
                    )
        charts.append(
            backtest_utils.LightweightChartsChart(
                title=f"{ticker} price ({base_scale})",
                series=price_series,
            )
        )
        charts.extend(ticker_aux)

    win_start_unix = int(win_start.timestamp())
    win_end_unix = int(win_end.timestamp())
    for spec in renko_specs:
        key = str(spec.id)
        bricks = renko_bricks.get(key, [])
        filtered = [
            (ut, o, c, d, bs)
            for (ut, o, c, d, bs) in bricks
            if win_start_unix <= ut < win_end_unix
        ]
        if not filtered:
            continue
        points = [
            backtest_utils.LwcCandlestickPoint(
                time=int(ut),
                open=float(o),
                high=float(max(o, c)),
                low=float(min(o, c)),
                close=float(c),
            )
            for (ut, o, c, _d, _bs) in filtered
        ]
        if spec.brick_size_mode == "atr":
            title = (
                f"{spec.ticker} renko bricks "
                f"(ATR {spec.atr_period} x {spec.atr_multiplier:g}, scale={spec.scale})"
            )
        else:
            title = (
                f"{spec.ticker} renko bricks "
                f"(brick_size={spec.brick_size}, scale={spec.scale})"
            )
        charts.append(
            backtest_utils.LightweightChartsChart(
                title=title,
                series=[
                    backtest_utils.LwcCandlestickSeries(
                        label=f"{spec.ticker} renko",
                        options={"upColor": "#26a69a", "downColor": "#ef5350"},
                        data=points,
                        markers=(
                            markers.get(spec.ticker, []) or None
                            if isinstance(markers, dict)
                            else ((markers or None) if spec.ticker == primary_ticker else None)
                        ),
                    )
                ],
            )
        )
    return charts


def _emit_ui(payload: dict) -> None:
    line = json.dumps({"simulation_ui": True, **payload}, default=str)
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _progress_emit_step_percent() -> int:
    raw = (os.environ.get("SIMULATION_PROGRESS_STEP_PERCENT") or "").strip()
    try:
        v = int(raw)
    except Exception:
        v = 10
    return max(1, min(25, v))


def _read_strategy_name(workspace: Path) -> str:
    params_path = workspace / "params.json"
    try:
        data = json.loads(params_path.read_text(encoding="utf-8"))
        name = data.get("strategy_name") or data.get("description")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except Exception:
        pass
    return workspace.name


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


def simulate(
    *,
    workspace: Path,
    start_d: date,
    end_d: date,
    initial_deposit: float,
    provider: str | None,
    entry_script: str,
    simulation_scale: str | None = None,
    max_leverage: float = 1.0,
) -> tuple[
    backtest_utils.DataJson,
    list[dict[str, str]] | None,
    OutputTrainedModelParams | None,
]:
    bars_query = HistoricalBarsQuery()
    rt = StrategyRuntime(workspace, entry_script=entry_script)
    strategy_subprocess_secs = 0.0
    trained_model_input = _trained_model_params_input_from_workspace(workspace)
    pending_initial_points = [trained_model_input] if trained_model_input is not None else []

    def _call_strategy(fn, *args, **kwargs):
        nonlocal strategy_subprocess_secs
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            strategy_subprocess_secs += time.perf_counter() - t0

    try:
        startup = _call_strategy(rt.start)
        extra_startup_outputs = _call_strategy(rt.drain_stdout, timeout_seconds=0.1)
        if extra_startup_outputs:
            startup = StrategyOutput(
                [
                    *startup.root,
                    *[
                        point
                        for output in extra_startup_outputs
                        for point in output.root
                    ],
                ]
            )
        startup = assign_subscription_ids(startup)
        logger.info(
            "subscriptions_from_strategy %s",
            json.dumps(startup.model_dump(mode="json")),
        )
        tickers, base_scale = _subscribed_tickers_and_base_scale(startup)
        logger.info(
            "startup workspace=%s entry_script=%s tickers=%s base_scale=%s",
            workspace,
            entry_script,
            tickers,
            base_scale,
        )
        sim_scale = (
            normalize_scale(simulation_scale)
            if simulation_scale
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
        ticker_set = set(tickers)
        for spec in ind_specs:
            st = getattr(spec, "ticker", None)
            if isinstance(st, str) and st.strip() and st.strip() not in ticker_set:
                raise ValueError(
                    f"Indicator subscription ticker {st!r} not in subscribed tickers {tickers!r}"
                )
        multi_ticker = len(tickers) > 1
        if multi_ticker:
            if sim_scale != base_scale:
                raise ValueError(
                    f"multi-ticker simulation requires simulation_scale ({sim_scale!r}) == scale ({base_scale!r})"
                )
            if any(getattr(s, "kind", None) == "renko" for s in ind_specs):
                raise ValueError(
                    "multi-ticker simulation does not support renko subscriptions yet"
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

        per_driver_df: dict[str, pd.DataFrame] = {}
        per_base_df: dict[str, pd.DataFrame] = {}
        per_engine: dict[str, IndicatorEngine] = {}
        per_engine_ind_subs: dict[str, list] = {}
        subscription_input_download_secs = 0.0
        dl_t0 = time.perf_counter()
        for t in tickers:
            logger.info(
                "fetch bars ticker=%s sim_scale=%s start=%s end=%s padding_days=%s provider=%s",
                t,
                sim_scale,
                start_d,
                end_d,
                padding,
                provider,
            )
            driver_df_t, _ = bars_query.fetch_chunked_merge(
                t, sim_scale, start_d, end_d, padding_days=padding, provider=provider
            )
            if driver_df_t.empty:
                logger.warning("no OHLC rows for ticker=%s; skipping", t)
                continue
            base_df_t = (
                driver_df_t
                if sim_scale == base_scale
                else aggregate_to_base(driver_df_t, base_scale)
            )
            if base_df_t.empty:
                logger.warning(
                    "no base OHLC rows after aggregation for ticker=%s; skipping", t
                )
                continue
            per_driver_df[t] = driver_df_t
            per_base_df[t] = base_df_t
            ind_for_t = [
                s
                for s in ind_specs
                if getattr(s, "ticker", None) == t
                and getattr(s, "kind", None) != "renko"
            ]
            per_engine_ind_subs[t] = ind_for_t
            engine_t = IndicatorEngine(ind_for_t)
            engine_t.fit(base_df_t)
            per_engine[t] = engine_t
            logger.info(
                "driver_df ticker=%s rows=%s base_rows=%s columns=%s",
                t,
                len(driver_df_t),
                len(base_df_t),
                list(driver_df_t.columns),
            )
        subscription_input_download_secs = time.perf_counter() - dl_t0
        logger.info(
            "subscription_input_download_seconds=%.3f tickers=%s",
            subscription_input_download_secs,
            tickers,
        )

        if not per_driver_df:
            raise ValueError("No OHLC rows returned for simulation")

        primary_ticker = (
            tickers[0] if tickers[0] in per_base_df else next(iter(per_base_df))
        )
        start_i, end_i = _simulation_row_range(per_base_df[primary_ticker], start_d, end_d)
        total_units = max(0, int(end_i) - int(start_i) + 1)
        progress_step = _progress_emit_step_percent()
        next_progress_pct = 0
        completed_units = 0
        logger.info(
            "primary_ticker=%s base_scale=%s simulation_row_range=[%s,%s] requested=[%s,%s]",
            primary_ticker,
            base_scale,
            start_i,
            end_i,
            start_d,
            end_d,
        )
        _emit_ui(
            {
                "event": "start",
                "workspace": str(workspace),
                "entry_script": entry_script,
                "tickers": tickers,
                "base_scale": base_scale,
                "simulation_scale": sim_scale,
                "start_date": str(start_d),
                "end_date": str(end_d),
                "total_units": total_units,
                "progress_step_percent": progress_step,
            }
        )
        portfolio = Portfolio(
            initial_deposit=initial_deposit,
            ticker=primary_ticker,
            max_leverage=max_leverage,
        )

        markers: dict[str, list[backtest_utils.LwcMarker]] = {}
        equity_points: list[backtest_utils.LwcTimeValuePoint] = []
        bench_points: list[backtest_utils.LwcTimeValuePoint] = []
        table_rows: list[dict] = []
        bench_first_close: float | None = None
        strategy_charts: list[dict] = []
        output_indicator_points: dict[str, list[tuple[int, float]]] = {}
        traded_tickers: set[str] = set()
        position_value_points: dict[str, list[backtest_utils.LwcTimeValuePoint]] = {}
        renko_bricks: dict[str, list[tuple[int, float, float, str, float]]] = {}
        trained_model_params: OutputTrainedModelParams | None = None

        def _collect_strategy_charts(output: StrategyOutput) -> None:
            for item in output.root:
                if isinstance(item, OutputChart):
                    strategy_charts.append(item.chart.model_dump(mode="json"))

        def _collect_trained_model_params(output: StrategyOutput) -> None:
            nonlocal trained_model_params
            for item in output.root:
                if isinstance(item, OutputTrainedModelParams):
                    trained_model_params = item

        def _record_new_trades(first_trade_index: int, t_ax: str | int) -> None:
            for trade in portfolio.trades[first_trade_index:]:
                is_invalid = not trade.valid
                if trade.valid:
                    traded_tickers.add(trade.ticker)
                is_buy = trade.action in {"buy", "buy_to_cover"}
                markers.setdefault(trade.ticker, []).append(
                    backtest_utils.LwcMarker(
                        time=t_ax,
                        position=(
                            "inBar"
                            if is_invalid
                            else ("belowBar" if is_buy else "aboveBar")
                        ),
                        color=(
                            "#9e9e9e"
                            if is_invalid
                            else ("#26a69a" if is_buy else "#ef5350")
                        ),
                        shape=(
                            "circle"
                            if is_invalid
                            else ("arrowUp" if is_buy else "arrowDown")
                        ),
                        text="ERROR" if is_invalid else trade.label,
                    )
                )
                table_rows.append(
                    {
                        "time": pd.Timestamp(
                            trade.unixtime, unit="s", tz="UTC"
                        ).isoformat(),
                        "ticker": trade.ticker,
                        "direction": trade.direction,
                        "price": round(trade.price, 6),
                        "qty": round(trade.qty, 6),
                        "deposit_ratio": round(trade.deposit_ratio, 6),
                        "position_before_order": round(trade.position_before_order, 6),
                        "position_after_order_filled": round(
                            trade.position_after_order_filled, 6
                        ),
                        "status": "invalid" if is_invalid else "filled",
                        "comment": trade.reason or "strategy signal",
                    }
                )

        def _record_position_values(t_ax: str | int, marks: dict[str, float]) -> None:
            for ticker in sorted(traded_tickers):
                pos = portfolio.positions.get(ticker)
                px = marks.get(ticker)
                if px is None:
                    px = portfolio.last_marks.get(
                        ticker, pos.avg_entry_price if pos is not None else 0.0
                    )
                value = 0.0 if pos is None else float(pos.qty) * float(px)
                position_value_points.setdefault(ticker, []).append(
                    backtest_utils.LwcTimeValuePoint(time=t_ax, value=value)
                )

        def _apply_outputs(
            out: StrategyOutput,
            *,
            step_unixtime: int,
            fills: dict[str, float],
        ) -> None:
            _collect_strategy_charts(out)
            _collect_trained_model_params(out)
            for item in out.root:
                if isinstance(item, OutputIndicatorDataPoint):
                    output_indicator_points.setdefault(item.name, []).append(
                        (int(item.unixtime), float(item.value))
                    )
                    continue
            orders = [item for item in out.root if isinstance(item, OutputMarketTradeOrder)]
            if not orders:
                return
            prices = dict(fills)
            if not multi_ticker:
                primary_px = prices.get(primary_ticker)
                if primary_px is not None:
                    for item in orders:
                        prices.setdefault(item.ticker, primary_px)
            portfolio.apply_market_orders(
                orders,
                prices=prices,
                unixtime=step_unixtime,
                reason="strategy",
            )

        _collect_strategy_charts(startup)
        _collect_trained_model_params(startup)

        if not multi_ticker:
            driver_df = per_driver_df[primary_ticker]
            base_df = per_base_df[primary_ticker]
            engine = per_engine[primary_ticker]
            ticker_subs, indicator_subs, renko_subs = compile_subscriptions(
                startup, base_scale, sim_scale
            )
            logger.info(
                "subscriptions_compiled_ticker %s",
                json.dumps(
                    [
                        {
                            "ticker": s.ticker,
                            "scale": s.scale,
                            "update_scale": s.update_scale,
                        }
                        for s in ticker_subs
                    ]
                ),
            )
            logger.info(
                "subscriptions_compiled_indicator %s",
                json.dumps(
                    [
                        {
                            "ticker": s.ticker,
                            "scale": s.scale,
                            "update_scale": s.update_scale,
                            "indicator_name": s.indicator_name,
                        }
                        for s in indicator_subs
                    ]
                ),
            )
            renko_log_rows: list[dict] = []
            for s in renko_subs:
                src = s.source
                brick_size = getattr(src, "brick_size", None)
                renko_log_rows.append(
                    {
                        "id": s.id,
                        "ticker": s.ticker,
                        "scale": s.scale,
                        "update_scale": s.update_scale,
                        "brick_size_mode": getattr(src, "brick_size_mode", "fixed"),
                        "brick_size": float(brick_size)
                        if brick_size is not None
                        else None,
                        "atr_period": getattr(src, "atr_period", None),
                        "atr_multiplier": getattr(src, "atr_multiplier", None),
                    }
                )
            logger.info(
                "subscriptions_compiled_renko %s",
                json.dumps(renko_log_rows),
            )
            for step in iter_simulation_steps(
                driver_df=driver_df,
                base_df=base_df,
                base_scale=base_scale,
                simulation_scale=sim_scale,
                ticker_subs=ticker_subs,
                indicator_subs=indicator_subs,
                indicator_engine=engine,
                renko_subs=renko_subs,
            ):
                fill_price = step.running.close
                pre_trade_count = len(portfolio.trades)
                in_requested_window = start_i <= step.base_row <= end_i
                if step.fired and in_requested_window:
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
                        for pt in line.points:
                            if isinstance(pt, InputRenkoDataPoint):
                                renko_bricks.setdefault(
                                    str(pt.id), []
                                ).append(
                                    (
                                        int(line.unixtime),
                                        float(pt.open),
                                        float(pt.close),
                                        str(pt.direction),
                                        float(pt.brick_size),
                                    )
                                )
                        out = _call_strategy(rt.send, line)
                        _apply_outputs(
                            out,
                            step_unixtime=line.unixtime,
                            fills={primary_ticker: fill_price},
                        )
                marks = {primary_ticker: fill_price}
                if in_requested_window:
                    portfolio.record_equity(step.unixtime, marks)

                if step.is_base_close and in_requested_window:
                    completed_units += 1
                    if total_units > 0:
                        pct = int((completed_units * 100) // total_units)
                        if pct >= next_progress_pct:
                            _emit_ui(
                                {
                                    "event": "progress",
                                    "percent": min(100, pct),
                                    "completed_units": completed_units,
                                    "total_units": total_units,
                                    "unixtime": int(step.unixtime),
                                    "base_row": int(step.base_row),
                                }
                            )
                            next_progress_pct = min(100, pct + progress_step)
                    base_unix = int(pd.Timestamp(step.base_ts).timestamp())
                    t_ax = _time_for_chart(base_unix, base_scale)
                    eq = portfolio.equity(marks)
                    equity_points.append(
                        backtest_utils.LwcTimeValuePoint(time=t_ax, value=float(eq))
                    )
                    if bench_first_close is None:
                        bench_first_close = float(step.running.close)
                    bench_val = initial_deposit * (
                        float(step.running.close) / bench_first_close
                    )
                    bench_points.append(
                        backtest_utils.LwcTimeValuePoint(time=t_ax, value=float(bench_val))
                    )
                    _record_new_trades(pre_trade_count, t_ax)
                    _record_position_values(t_ax, marks)
        else:
            ticker_sub_order: list[OutputTickerSubscription] = [
                p for p in startup.root if isinstance(p, OutputTickerSubscription)
            ]
            indicator_sub_order = [
                p.indicator
                for p in startup.root
                if isinstance(p, OutputIndicatorSubscriptionOrder)
            ]
            ts_to_row: dict[str, dict[pd.Timestamp, int]] = {}
            for tkr, df in per_base_df.items():
                m: dict[pd.Timestamp, int] = {}
                for i, ts in enumerate(df.index):
                    ts_utc = pd.Timestamp(ts)
                    if ts_utc.tzinfo is None:
                        ts_utc = ts_utc.tz_localize("UTC")
                    m[ts_utc] = i
                ts_to_row[tkr] = m
            all_ts_set: set[pd.Timestamp] = set()
            for m in ts_to_row.values():
                all_ts_set.update(m.keys())
            all_ts_sorted = sorted(all_ts_set)
            logger.info(
                "multi_ticker union_timeline bars=%s tickers=%s",
                len(all_ts_sorted),
                tickers,
            )
            primary_row_map = ts_to_row[primary_ticker]
            start_ts = pd.Timestamp(start_d).tz_localize("UTC")
            end_excl = pd.Timestamp(end_d).tz_localize("UTC") + pd.Timedelta(days=1)
            last_prices: dict[str, float] = {}
            total_units = sum(
                1
                for ts in all_ts_sorted
                if (r := primary_row_map.get(ts)) is not None and start_i <= r <= end_i
            )
            next_progress_pct = 0
            completed_units = 0
            for ts in all_ts_sorted:
                base_unix = int(ts.timestamp())
                t_ax = _time_for_chart(base_unix, base_scale)
                step_points: list = []
                fills: dict[str, float] = {}
                primary_bar: tuple[float, float, float, float] | None = None
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
                    v = (
                        float(df.iloc[row]["volume"])
                        if "volume" in df.columns
                        else 0.0
                    )
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
                        primary_bar = (o, h, l, c)
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
                        step_points.append(
                            pt.model_copy(update={"id": str(ind_sub.id)})
                        )
                fired = bool(step_points)
                primary_row = primary_row_map.get(ts)
                in_requested_window = start_ts <= ts < end_excl

                pre_trade_count = len(portfolio.trades)
                if fired and in_requested_window:
                    if last_prices:
                        portfolio.equity(last_prices)
                    points_all: list = [
                        portfolio.to_portfolio_datapoint(),
                        *pending_initial_points,
                        *step_points,
                    ]
                    pending_initial_points = []
                    step_input = StrategyInput(unixtime=base_unix, points=points_all)
                    out = _call_strategy(rt.send, step_input)
                    _apply_outputs(out, step_unixtime=base_unix, fills=fills)

                primary_close = fills.get(primary_ticker)
                if last_prices and in_requested_window:
                    portfolio.record_equity(base_unix, last_prices)

                if (
                    primary_row is not None
                    and primary_bar is not None
                    and start_i <= primary_row <= end_i
                ):
                    completed_units += 1
                    if total_units > 0:
                        pct = int((completed_units * 100) // total_units)
                        if pct >= next_progress_pct:
                            _emit_ui(
                                {
                                    "event": "progress",
                                    "percent": min(100, pct),
                                    "completed_units": completed_units,
                                    "total_units": total_units,
                                    "unixtime": int(base_unix),
                                    "base_row": int(primary_row),
                                }
                            )
                            next_progress_pct = min(100, pct + progress_step)
                    c = primary_bar[3]
                    eq = portfolio.equity(last_prices)
                    equity_points.append(
                        backtest_utils.LwcTimeValuePoint(time=t_ax, value=float(eq))
                    )
                    if bench_first_close is None:
                        bench_first_close = c
                    bench_val = initial_deposit * (c / bench_first_close)
                    bench_points.append(
                        backtest_utils.LwcTimeValuePoint(time=t_ax, value=float(bench_val))
                    )
                    _record_new_trades(pre_trade_count, t_ax)
                    _record_position_values(t_ax, last_prices)
        try:
            final_output = _call_strategy(rt.finalize)
        except StrategyRuntimeError as exc:
            logger.warning("strategy finalize failed: %s", exc)
            final_output = StrategyOutput([])
        _collect_strategy_charts(final_output)
        _collect_trained_model_params(final_output)
        logger.info("strategy_subprocess_seconds=%.3f", strategy_subprocess_secs)
    finally:
        try:
            rt.write_io_files()
        except Exception as exc:
            logger.warning("failed to write inputs.json/outputs.json: %s", exc)
        rt.close()

    if total_units > 0 and completed_units != total_units:
        completed_units = total_units
    _emit_ui(
        {
            "event": "done",
            "percent": 100,
            "completed_units": completed_units,
            "total_units": total_units,
        }
    )

    equity_series_values = [p.value for p in equity_points]
    final_equity = equity_series_values[-1] if equity_series_values else initial_deposit
    total_return = final_equity / initial_deposit - 1.0
    max_dd = _max_drawdown(equity_series_values)
    sharpe = _sharpe_ratio(equity_series_values, scale=base_scale)

    win_rate = _trade_win_rate(portfolio.trades)

    strategy_name = _read_strategy_name(workspace)

    startup_with_ids = assign_subscription_ids(startup)
    renko_specs: list[RenkoIndicatorSubscription] = [
        p.indicator
        for p in startup_with_ids.root
        if isinstance(p, OutputIndicatorSubscriptionOrder)
        and isinstance(p.indicator, RenkoIndicatorSubscription)
    ]

    subscription_charts = _build_subscription_charts(
        tickers=tickers,
        base_scale=base_scale,
        per_base_df=per_base_df,
        per_engine=per_engine,
        per_engine_ind_subs=per_engine_ind_subs,
        primary_ticker=primary_ticker,
        start_d=start_d,
        end_d=end_d,
        markers=markers,
        output_indicator_points=output_indicator_points,
        renko_specs=renko_specs,
        renko_bricks=renko_bricks,
    )

    catalog_json_for_backtest: list[dict[str, str]] | None = None
    for p in startup.root:
        if isinstance(p, OutputIndicatorSeriesCatalog):
            if p.series:
                catalog_json_for_backtest = [
                    e.model_dump(mode="json") for e in p.series
                ]
            break

    equity_chart = backtest_utils.LightweightChartsChart(
        title="Equity curve vs buy & hold",
        series=[
            backtest_utils.LwcTimeValueSeries(
                type="Line",
                label="Strategy equity",
                options={"color": "#2962ff", "lineWidth": 2},
                data=equity_points,
                markers=(
                    [
                        marker
                        for ticker_markers in markers.values()
                        for marker in ticker_markers
                    ]
                    or None
                ),
            ),
            backtest_utils.LwcTimeValueSeries(
                type="Line",
                label=f"Buy & hold {primary_ticker}",
                options={"color": "#9e9e9e", "lineWidth": 2},
                data=bench_points,
            ),
        ],
    )
    position_value_chart = _build_position_value_chart(position_value_points)
    trades_chart = backtest_utils.TableChart(title="Orders", rows=table_rows)

    metrics = backtest_utils.Metrics(
        total_return=float(total_return) * 100.0,
        sharpe_ratio=(float(sharpe) if sharpe is not None else None),
        max_drawdown=float(max_dd) * 100.0,
        win_rate=(float(win_rate) * 100.0 if win_rate is not None else None),
        num_trades=sum(1 for trade in portfolio.trades if trade.valid),
        final_equity=float(final_equity),
    )

    logger.info(
        "done strategy_name=%s trades=%s total_return=%s max_drawdown=%s strategy_charts=%s subscription_charts=%s",
        strategy_name,
        metrics.num_trades,
        metrics.total_return,
        metrics.max_drawdown,
        len(strategy_charts),
        len(subscription_charts),
    )
    return (
        backtest_utils.DataJson(
            strategy_name=strategy_name,
            charts=[
                *subscription_charts,
                *strategy_charts,
                equity_chart,
                *([position_value_chart] if position_value_chart is not None else []),
                trades_chart,
            ],
            metrics=metrics,
        ),
        catalog_json_for_backtest,
        trained_model_params,
    )


def _write_workspace_outputs(
    doc: backtest_utils.DataJson,
    workspace: Path,
    *,
    indicator_series_catalog: list[dict[str, str]] | None = None,
    trained_model_params: OutputTrainedModelParams | None = None,
) -> tuple[Path, Path | None]:
    serialized = doc.model_dump(mode="json", exclude_none=True)
    if indicator_series_catalog:
        serialized["indicator_series_catalog"] = indicator_series_catalog
    metrics = serialized.pop("metrics", None)
    backtest_path = workspace / "backtest.json"
    metrics_path = workspace / "metrics.json"
    workspace.mkdir(parents=True, exist_ok=True)
    backtest_path.write_text(
        json.dumps(serialized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if trained_model_params is not None:
        (workspace / "trained_model_params.json").write_text(
            trained_model_params.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    if metrics is None:
        try:
            metrics_path.unlink()
        except FileNotFoundError:
            pass
        return backtest_path, None
    metrics_path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return backtest_path, metrics_path


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(
        description="Run a strategies_v2 strategy against historical bars (no pacing) and write backtest.json + metrics.json next to the entry script."
    )
    parser.add_argument(
        "--entry",
        required=True,
        help="Path to the strategy entry script (e.g. strategy.py). Its parent directory is the workspace; start_date, end_date, initial_deposit, provider, and simulation_scale are read from params.json there. backtest.json and metrics.json are written there.",
    )
    args = parser.parse_args(argv)

    entry_path = Path(args.entry).resolve()
    if not entry_path.is_file():
        parser.error(f"--entry must be an existing file: {entry_path}")
    workspace = entry_path.parent
    entry_script = entry_path.relative_to(workspace).as_posix()
    params_path = workspace / "params.json"
    try:
        params = json.loads(params_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        parser.error(f"params.json not found in workspace: {params_path}")
    except json.JSONDecodeError as exc:
        parser.error(f"invalid params.json: {exc}")

    start_raw = params.get("start_date")
    end_raw = params.get("end_date")
    deposit_raw = params.get("initial_deposit")
    if not start_raw or not end_raw:
        parser.error("params.json must define 'start_date' and 'end_date'")
    if deposit_raw is None:
        parser.error("params.json must define 'initial_deposit'")

    start_d = date.fromisoformat(str(start_raw))
    end_d = date.fromisoformat(str(end_raw))
    if start_d > end_d:
        parser.error("params.json 'start_date' must be on or before 'end_date'")
    deposit = float(deposit_raw)
    if deposit <= 0:
        parser.error("params.json 'initial_deposit' must be positive")

    provider = params.get("provider")
    simulation_scale = params.get("simulation_scale")
    max_leverage = read_strategy_max_leverage(params_path)
    logger.info(
        "run entry=%s workspace=%s start=%s end=%s initial_deposit=%s "
        "provider=%s simulation_scale=%s max_leverage=%s",
        entry_path,
        workspace,
        start_d,
        end_d,
        deposit,
        provider,
        simulation_scale,
        max_leverage,
    )

    doc, indicator_catalog, trained_model_params = simulate(
        workspace=workspace,
        start_d=start_d,
        end_d=end_d,
        initial_deposit=deposit,
        provider=provider,
        entry_script=entry_script,
        simulation_scale=simulation_scale,
        max_leverage=max_leverage,
    )
    backtest_path, metrics_path = _write_workspace_outputs(
        doc,
        workspace,
        indicator_series_catalog=indicator_catalog,
        trained_model_params=trained_model_params,
    )
    print(f"wrote {backtest_path}", file=sys.stderr)
    if metrics_path is not None:
        print(f"wrote {metrics_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
