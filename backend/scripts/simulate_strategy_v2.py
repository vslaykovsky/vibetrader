from __future__ import annotations

import argparse
import json
import logging
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
from application.services.strategy_runtime import StrategyRuntime, StrategyRuntimeError
from application.use_cases.strategy_simulate import (
    _indicator_subscriptions_from_startup,
    _padding_days_for_indicator_subscriptions,
    _read_simulation_scale,
    _simulation_row_range,
)
from strategies import utils as backtest_utils
from strategies_v2.utils import (
    InputPortfolioDataPoint,
    InputOhlcDataPoint,
    InputRenkoDataPoint,
    Ohlc,
    OutputChart,
    OutputIndicatorDataPoint,
    OutputIndicatorSubscriptionOrder,
    OutputMarketTradeOrder,
    OutputTickerSubscription,
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
    markers: list[backtest_utils.LwcMarker],
    output_indicator_points: dict[str, list[tuple[int, float]]],
    renko_specs: list[RenkoIndicatorSubscription],
    renko_bricks: dict[tuple[str, float], list[tuple[int, float, float, str]]],
) -> list[backtest_utils.LightweightChartsChart]:
    win_start = pd.Timestamp(start_d, tz="UTC")
    win_end = pd.Timestamp(end_d, tz="UTC") + pd.Timedelta(days=1)
    charts: list[backtest_utils.LightweightChartsChart] = []
    for ticker in tickers:
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
                markers=(markers or None) if ticker == primary_ticker else None,
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
        key = (spec.ticker, float(spec.brick_size))
        bricks = renko_bricks.get(key, [])
        filtered = [
            (ut, o, c, d)
            for (ut, o, c, d) in bricks
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
            for (ut, o, c, _d) in filtered
        ]
        charts.append(
            backtest_utils.LightweightChartsChart(
                title=f"{spec.ticker} renko bricks (brick_size={spec.brick_size}, scale={spec.scale})",
                series=[
                    backtest_utils.LwcCandlestickSeries(
                        label=f"{spec.ticker} renko",
                        options={"upColor": "#26a69a", "downColor": "#ef5350"},
                        data=points,
                    )
                ],
            )
        )
    return charts


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


def simulate(
    *,
    workspace: Path,
    start_d: date,
    end_d: date,
    initial_deposit: float,
    provider: str | None,
    entry_script: str,
    simulation_scale: str | None = None,
) -> backtest_utils.DataJson:
    cache_dir = workspace / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    bars_query = HistoricalBarsQuery(cache_dir=cache_dir)
    rt = StrategyRuntime(workspace, entry_script=entry_script)
    strategy_subprocess_secs = 0.0

    def _call_strategy(fn, *args, **kwargs):
        nonlocal strategy_subprocess_secs
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            strategy_subprocess_secs += time.perf_counter() - t0

    try:
        startup = _call_strategy(
            rt.start,
            initial_input=StrategyInput(
                unixtime=0,
                points=[InputPortfolioDataPoint(positions=[])],
            ),
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
        logger.info(
            "primary_ticker=%s base_scale=%s simulation_row_range=[%s,%s] requested=[%s,%s]",
            primary_ticker,
            base_scale,
            start_i,
            end_i,
            start_d,
            end_d,
        )
        portfolio = Portfolio(initial_deposit=initial_deposit, ticker=primary_ticker)

        markers: list[backtest_utils.LwcMarker] = []
        equity_points: list[backtest_utils.LwcTimeValuePoint] = []
        bench_points: list[backtest_utils.LwcTimeValuePoint] = []
        table_rows: list[dict] = []
        bench_first_close: float | None = None
        strategy_charts: list[dict] = []
        output_indicator_points: dict[str, list[tuple[int, float]]] = {}
        renko_bricks: dict[tuple[str, float], list[tuple[int, float, float, str]]] = {}

        def _collect_strategy_charts(output: StrategyOutput) -> None:
            for item in output.root:
                if isinstance(item, OutputChart):
                    strategy_charts.append(item.chart.model_dump(mode="json"))

        def _apply_outputs(
            out: StrategyOutput,
            *,
            step_unixtime: int,
            fills: dict[str, float],
        ) -> None:
            _collect_strategy_charts(out)
            for item in out.root:
                if isinstance(item, OutputIndicatorDataPoint):
                    output_indicator_points.setdefault(item.name, []).append(
                        (int(item.unixtime), float(item.value))
                    )
                    continue
                if isinstance(item, OutputMarketTradeOrder):
                    if multi_ticker:
                        raise ValueError(
                            "multi-ticker market_order execution is not supported yet"
                        )
                    fill_px = fills.get(item.ticker)
                    if fill_px is None:
                        fill_px = fills.get(primary_ticker)
                    if fill_px is None:
                        raise ValueError(
                            f"no fill price available for ticker {item.ticker!r}"
                        )
                    portfolio.apply_market_order(
                        direction=item.direction,
                        deposit_ratio=item.deposit_ratio,
                        price=fill_px,
                        unixtime=step_unixtime,
                        reason="strategy",
                    )

        _collect_strategy_charts(startup)

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
            logger.info(
                "subscriptions_compiled_renko %s",
                json.dumps(
                    [
                        {
                            "ticker": s.ticker,
                            "scale": s.scale,
                            "update_scale": s.update_scale,
                            "brick_size": float(
                                getattr(s.source, "brick_size", 0.0)
                            ),
                        }
                        for s in renko_subs
                    ]
                ),
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
                if step.fired:
                    for line in expand_step_to_lines(
                        step,
                        portfolio_provider=portfolio.to_portfolio_datapoint,
                    ):
                        for pt in line.points:
                            if isinstance(pt, InputRenkoDataPoint):
                                renko_bricks.setdefault(
                                    (pt.ticker, float(pt.brick_size)), []
                                ).append(
                                    (
                                        int(line.unixtime),
                                        float(pt.open),
                                        float(pt.close),
                                        str(pt.direction),
                                    )
                                )
                        out = _call_strategy(rt.send, line)
                        _apply_outputs(
                            out,
                            step_unixtime=line.unixtime,
                            fills={primary_ticker: fill_price},
                        )
                portfolio.record_equity(step.unixtime, fill_price)

                if step.is_base_close and start_i <= step.base_row <= end_i:
                    base_unix = int(pd.Timestamp(step.base_ts).timestamp())
                    t_ax = _time_for_chart(base_unix, base_scale)
                    eq = portfolio.equity(fill_price)
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
                    for trade in portfolio.trades[pre_trade_count:]:
                        is_buy = trade.direction == "buy"
                        markers.append(
                            backtest_utils.LwcMarker(
                                time=t_ax,
                                position="belowBar" if is_buy else "aboveBar",
                                color="#26a69a" if is_buy else "#ef5350",
                                shape="arrowUp" if is_buy else "arrowDown",
                                text=("BUY" if is_buy else "SELL"),
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
                                "comment": trade.reason or "strategy signal",
                            }
                        )
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
                            pt.model_copy(
                                update={"id": str(ind_sub.id), "ticker": str(t_ind)}
                            )
                        )
                fired = bool(step_points)
                primary_row = primary_row_map.get(ts)

                pre_trade_count = len(portfolio.trades)
                if fired:
                    points_all: list = [portfolio.to_portfolio_datapoint()] + step_points
                    step_input = StrategyInput(unixtime=base_unix, points=points_all)
                    out = _call_strategy(rt.send, step_input)
                    _apply_outputs(out, step_unixtime=base_unix, fills=fills)

                primary_close = fills.get(primary_ticker)
                if primary_close is not None:
                    portfolio.record_equity(base_unix, primary_close)

                if (
                    primary_row is not None
                    and primary_bar is not None
                    and start_i <= primary_row <= end_i
                ):
                    c = primary_bar[3]
                    eq = portfolio.equity(c)
                    equity_points.append(
                        backtest_utils.LwcTimeValuePoint(time=t_ax, value=float(eq))
                    )
                    if bench_first_close is None:
                        bench_first_close = c
                    bench_val = initial_deposit * (c / bench_first_close)
                    bench_points.append(
                        backtest_utils.LwcTimeValuePoint(time=t_ax, value=float(bench_val))
                    )
                    for trade in portfolio.trades[pre_trade_count:]:
                        is_buy = trade.direction == "buy"
                        markers.append(
                            backtest_utils.LwcMarker(
                                time=t_ax,
                                position="belowBar" if is_buy else "aboveBar",
                                color="#26a69a" if is_buy else "#ef5350",
                                shape="arrowUp" if is_buy else "arrowDown",
                                text=("BUY" if is_buy else "SELL"),
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
                                "comment": trade.reason or "strategy signal",
                            }
                        )
        try:
            final_output = _call_strategy(rt.finalize)
        except StrategyRuntimeError as exc:
            logger.warning("strategy finalize failed: %s", exc)
            final_output = StrategyOutput([])
        _collect_strategy_charts(final_output)
        logger.info("strategy_subprocess_seconds=%.3f", strategy_subprocess_secs)
    finally:
        try:
            rt.write_io_files()
        except Exception as exc:
            logger.warning("failed to write inputs.json/outputs.json: %s", exc)
        rt.close()

    is_eda = len(portfolio.trades) == 0
    equity_series_values = [p.value for p in equity_points]
    final_equity = equity_series_values[-1] if equity_series_values else initial_deposit
    total_return = final_equity / initial_deposit - 1.0
    max_dd = _max_drawdown(equity_series_values)
    sharpe = _sharpe_ratio(equity_series_values, scale=base_scale)

    buys = [t for t in portfolio.trades if t.direction == "buy"]
    sells = [t for t in portfolio.trades if t.direction == "sell"]
    paired = min(len(buys), len(sells))
    wins = sum(1 for b, s in zip(buys, sells) if s.price > b.price)
    win_rate = (wins / paired) if paired > 0 else None

    strategy_name = _read_strategy_name(workspace)

    renko_specs: list[RenkoIndicatorSubscription] = [
        p.indicator
        for p in startup.root
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

    if is_eda:
        logger.info(
            "done eda strategy_name=%s strategy_charts=%s subscription_charts=%s",
            strategy_name,
            len(strategy_charts),
            len(subscription_charts),
        )
        return backtest_utils.DataJson(
            strategy_name=strategy_name,
            charts=[*subscription_charts, *strategy_charts],
            metrics=None,
        )

    equity_chart = backtest_utils.LightweightChartsChart(
        title="Equity curve vs buy & hold",
        series=[
            backtest_utils.LwcTimeValueSeries(
                type="Line",
                label="Strategy equity",
                options={"color": "#2962ff", "lineWidth": 2},
                data=equity_points,
            ),
            backtest_utils.LwcTimeValueSeries(
                type="Line",
                label=f"Buy & hold {primary_ticker}",
                options={"color": "#9e9e9e", "lineWidth": 2},
                data=bench_points,
            ),
        ],
    )
    trades_chart = backtest_utils.TableChart(title="Trades", rows=table_rows)

    metrics = backtest_utils.Metrics(
        total_return=float(total_return),
        sharpe_ratio=(float(sharpe) if sharpe is not None else None),
        max_drawdown=float(max_dd),
        win_rate=(float(win_rate) if win_rate is not None else None),
        num_trades=len(portfolio.trades),
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
    return backtest_utils.DataJson(
        strategy_name=strategy_name,
        charts=[
            *subscription_charts,
            *strategy_charts,
            equity_chart,
            trades_chart,
        ],
        metrics=metrics,
    )


def _write_workspace_outputs(
    doc: backtest_utils.DataJson, workspace: Path
) -> tuple[Path, Path | None]:
    serialized = doc.model_dump(mode="json", exclude_none=True)
    metrics = serialized.pop("metrics", None)
    backtest_path = workspace / "backtest.json"
    metrics_path = workspace / "metrics.json"
    workspace.mkdir(parents=True, exist_ok=True)
    backtest_path.write_text(
        json.dumps(serialized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
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
    logger.info(
        "run entry=%s workspace=%s start=%s end=%s initial_deposit=%s provider=%s simulation_scale=%s",
        entry_path,
        workspace,
        start_d,
        end_d,
        deposit,
        provider,
        simulation_scale,
    )

    doc = simulate(
        workspace=workspace,
        start_d=start_d,
        end_d=end_d,
        initial_deposit=deposit,
        provider=provider,
        entry_script=entry_script,
        simulation_scale=simulation_scale,
    )
    backtest_path, metrics_path = _write_workspace_outputs(doc, workspace)
    print(f"wrote {backtest_path}", file=sys.stderr)
    if metrics_path is not None:
        print(f"wrote {metrics_path}", file=sys.stderr)
    else:
        print("skipped metrics.json (EDA run)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
