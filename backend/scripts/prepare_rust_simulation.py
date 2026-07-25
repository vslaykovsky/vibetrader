from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import msgpack
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
from application.services.market_calendar import uses_xnys_calendar, xnys_session_dates
from application.services.scale_utils import is_finer_or_equal, normalize_scale, scale_divides
from application.services.simulation_driver import (
    aggregate_to_base,
    assign_subscription_ids,
    compile_subscriptions,
    expand_step_to_lines,
    iter_simulation_steps,
)
from application.services.simulation_limits import read_strategy_max_leverage
from application.use_cases.strategy_simulate import (
    _indicator_subscriptions_from_startup,
    _padding_days_for_indicator_subscriptions,
    _read_simulation_scale,
    _simulation_row_range,
)
from scripts.simulate_strategy_v2 import (
    _build_subscription_charts,
    _read_strategy_name,
    _subscribed_ticker_sessions,
    _subscribed_tickers_and_base_scale,
    _time_for_chart,
)
from strategies_v2.utils import (
    InputPortfolioDataPoint,
    InputRenkoDataPoint,
    OutputIndicatorSubscriptionOrder,
    OutputTickerSubscription,
    RenkoIndicatorSubscription,
    SmaIndicatorSubscription,
    StrategyOutput,
)


class RustOptimizerUnsupported(ValueError):
    pass


class StudyBarsQuery:
    """Serve candidate-specific slices from one maximum-warmup OHLC fetch."""

    def __init__(self, payload: dict[str, Any]) -> None:
        bars = payload.get("bars") or []
        index = pd.to_datetime(
            [int(bar["timestamp_ns"]) for bar in bars],
            unit="ns",
            utc=bool(payload.get("index_tz_aware")),
        )
        self._frame = pd.DataFrame(
            {
                "open": [float(bar["open"]) for bar in bars],
                "high": [float(bar["high"]) for bar in bars],
                "low": [float(bar["low"]) for bar in bars],
                "close": [float(bar["close"]) for bar in bars],
                "volume": [float(bar.get("volume", 0.0)) for bar in bars],
            },
            index=index,
        )
        self._ticker = str(payload["primary_ticker"])
        self._scale = str(payload["simulation_scale"])
        self._session = str(payload["session"])
        self._provider = payload.get("provider")
        self._max_padding_days = int(payload["max_padding_days"])

    def fetch_chunked_merge(
        self,
        ticker: str,
        scale: str,
        start: date,
        end: date,
        padding_days: int = 0,
        *,
        provider: str | None = None,
        session: str = "all",
        **_kwargs: Any,
    ) -> tuple[pd.DataFrame, int]:
        if (
            ticker != self._ticker
            or normalize_scale(scale) != self._scale
            or session != self._session
            or provider != self._provider
        ):
            raise ValueError("cached Rust study data does not match the requested market data")
        if int(padding_days) > self._max_padding_days:
            raise ValueError("cached Rust study data has insufficient indicator warmup")
        return _slice_frame(self._frame, start, end, int(padding_days)), 1


def _empty_portfolio() -> InputPortfolioDataPoint:
    return InputPortfolioDataPoint(cash=0.0, equity=0.0, buying_power=0.0, positions=[])


def _event(
    *,
    unixtime: int,
    points: list,
    fills: dict[str, float],
    marks: dict[str, float],
    invoke_strategy: bool,
    record_equity: bool,
    base_close: bool,
    chart_time: str | int,
    benchmark_close: float | None,
    base_row: int | None,
    mark_before_input: bool = False,
) -> dict:
    return {
        "unixtime": int(unixtime),
        "points": [point.model_dump(mode="json") for point in points],
        "fills": {str(key): float(value) for key, value in fills.items()},
        "marks": {str(key): float(value) for key, value in marks.items()},
        "invoke_strategy": bool(invoke_strategy),
        "record_equity": bool(record_equity),
        "base_close": bool(base_close),
        "chart_time": chart_time,
        "benchmark_close": (
            float(benchmark_close) if benchmark_close is not None else None
        ),
        "base_row": int(base_row) if base_row is not None else None,
        "mark_before_input": bool(mark_before_input),
    }


def _slice_frame(
    frame: pd.DataFrame,
    start: date,
    end: date,
    padding_days: int,
) -> pd.DataFrame:
    lower = pd.Timestamp(start - timedelta(days=int(padding_days)))
    upper = pd.Timestamp(end + timedelta(days=1))
    if getattr(frame.index, "tz", None) is not None:
        lower = lower.tz_localize("UTC")
        upper = upper.tz_localize("UTC")
    return frame[(frame.index >= lower) & (frame.index < upper)].copy()


def _study_topology(
    startup: StrategyOutput,
    params: dict[str, Any],
    workspace: Path,
) -> tuple[StrategyOutput, str, str, str, int]:
    startup = assign_subscription_ids(startup)
    tickers, base_scale = _subscribed_tickers_and_base_scale(startup)
    if len(tickers) != 1:
        raise RustOptimizerUnsupported("the in-process optimizer currently requires one ticker")
    simulation_scale = normalize_scale(
        params.get("simulation_scale") or _read_simulation_scale(workspace, base_scale)
    )
    if simulation_scale != base_scale:
        raise RustOptimizerUnsupported(
            "the in-process optimizer currently requires simulation_scale == scale"
        )
    sessions = _subscribed_ticker_sessions(startup)
    ticker = tickers[0]
    for point in startup.root:
        source = point.indicator if isinstance(point, OutputIndicatorSubscriptionOrder) else point
        if getattr(source, "partial", False):
            raise RustOptimizerUnsupported(
                "the in-process optimizer currently requires closed subscriptions"
            )
    indicators = _indicator_subscriptions_from_startup(startup)
    if any(not isinstance(spec, SmaIndicatorSubscription) for spec in indicators):
        raise RustOptimizerUnsupported(
            "the in-process optimizer currently supports SMA subscriptions only"
        )
    return (
        startup,
        ticker,
        base_scale,
        sessions.get(ticker, "all"),
        _padding_days_for_indicator_subscriptions(indicators),
    )


def prepare_study(
    workspace: Path,
    startups: list[StrategyOutput],
) -> dict[str, Any]:
    if not startups:
        raise ValueError("Rust optimization study has no trial startups")
    params = json.loads((workspace / "params.json").read_text(encoding="utf-8"))
    start_d = date.fromisoformat(str(params["start_date"]))
    end_d = date.fromisoformat(str(params["end_date"]))
    topologies = [_study_topology(startup, params, workspace) for startup in startups]
    first = topologies[0]
    topology_key = first[1:4]
    if any(topology[1:4] != topology_key for topology in topologies[1:]):
        raise RustOptimizerUnsupported(
            "optimized parameters must not change ticker, scale, or session"
        )
    _, ticker, scale, session, _ = first
    max_padding_days = max(topology[4] for topology in topologies)
    provider = params.get("provider")
    market_calendar = (
        "XNYS"
        if uses_xnys_calendar(
            ticker=ticker,
            provider=provider,
            asset_class=params.get("asset_class"),
        )
        else None
    )
    market_sessions = (
        sorted(session_date.isoformat() for session_date in xnys_session_dates(start_d, end_d))
        if market_calendar == "XNYS"
        else []
    )
    query = HistoricalBarsQuery()
    frame, _ = query.fetch_chunked_merge(
        ticker,
        scale,
        start_d,
        end_d,
        padding_days=max_padding_days,
        provider=provider,
        session=session,
    )
    if frame.empty:
        raise ValueError("No OHLC rows returned for Rust optimization")
    frame = frame.sort_index()
    windows: list[dict[str, int]] = []
    for topology in topologies:
        candidate = _slice_frame(frame, start_d, end_d, topology[4])
        if candidate.empty:
            raise ValueError("No OHLC rows returned for a Rust optimization trial")
        start_i, end_i = _simulation_row_range(candidate, start_d, end_d)
        warmup_start = int(frame.index.get_indexer([candidate.index[0]])[0])
        windows.append(
            {
                "warmup_start": warmup_start,
                "simulation_start": warmup_start + int(start_i),
                "simulation_end": warmup_start + int(end_i),
            }
        )
    tz_aware = getattr(frame.index, "tz", None) is not None
    bars = []
    for timestamp, row in frame.iterrows():
        ts = pd.Timestamp(timestamp)
        bars.append(
            {
                "timestamp_ns": int(ts.value),
                "unixtime": int(ts.timestamp()),
                "chart_time": _time_for_chart(int(ts.timestamp()), scale),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0)),
            }
        )
    return {
        "strategy_name": _read_strategy_name(workspace),
        "primary_ticker": ticker,
        "base_scale": scale,
        "simulation_scale": scale,
        "session": session,
        "provider": provider,
        "market_calendar": market_calendar,
        "market_sessions": market_sessions,
        "initial_deposit": float(params["initial_deposit"]),
        "max_leverage": read_strategy_max_leverage(workspace / "params.json"),
        "max_padding_days": max_padding_days,
        "index_tz_aware": tz_aware,
        "bars": bars,
        "trial_windows": windows,
    }


def prepare(
    workspace: Path,
    startup: StrategyOutput,
    *,
    query: Any | None = None,
) -> dict:
    params_path = workspace / "params.json"
    params = json.loads(params_path.read_text(encoding="utf-8"))
    start_d = date.fromisoformat(str(params["start_date"]))
    end_d = date.fromisoformat(str(params["end_date"]))
    initial_deposit = float(params["initial_deposit"])
    provider = params.get("provider")
    max_leverage = read_strategy_max_leverage(params_path)

    startup = assign_subscription_ids(startup)
    tickers, base_scale = _subscribed_tickers_and_base_scale(startup)
    sessions = _subscribed_ticker_sessions(startup)
    simulation_scale = normalize_scale(
        params.get("simulation_scale")
        or _read_simulation_scale(workspace, base_scale)
    )
    if not is_finer_or_equal(simulation_scale, base_scale):
        raise ValueError(
            f"simulation_scale {simulation_scale!r} must be at most as coarse as scale {base_scale!r}"
        )
    if not scale_divides(simulation_scale, base_scale):
        raise ValueError(
            f"simulation_scale {simulation_scale!r} must divide scale {base_scale!r}"
        )

    indicator_specs = _indicator_subscriptions_from_startup(startup)
    ticker_set = set(tickers)
    for spec in indicator_specs:
        ticker = getattr(spec, "ticker", None)
        if isinstance(ticker, str) and ticker.strip() not in ticker_set:
            raise ValueError(
                f"Indicator subscription ticker {ticker!r} not in subscribed tickers {tickers!r}"
            )
    multi_ticker = len(tickers) > 1
    if multi_ticker:
        if simulation_scale != base_scale:
            raise ValueError("multi-ticker simulation requires simulation_scale == scale")
        if any(getattr(spec, "kind", None) == "renko" for spec in indicator_specs):
            raise ValueError("multi-ticker simulation does not support renko subscriptions")
        for point in startup.root:
            source = point.indicator if isinstance(point, OutputIndicatorSubscriptionOrder) else point
            if isinstance(source, OutputTickerSubscription) or isinstance(
                point, OutputIndicatorSubscriptionOrder
            ):
                if getattr(source, "partial", False):
                    raise ValueError("multi-ticker simulation does not support partial subscriptions")

    padding = _padding_days_for_indicator_subscriptions(indicator_specs)
    query = query or HistoricalBarsQuery()
    per_driver_df: dict[str, pd.DataFrame] = {}
    per_base_df: dict[str, pd.DataFrame] = {}
    per_engine: dict[str, IndicatorEngine] = {}
    per_engine_ind_subs: dict[str, list] = {}
    for ticker in tickers:
        driver_df, _ = query.fetch_chunked_merge(
            ticker,
            simulation_scale,
            start_d,
            end_d,
            padding_days=padding,
            provider=provider,
            session=sessions.get(ticker, "all"),
        )
        if driver_df.empty:
            continue
        base_df = (
            driver_df
            if simulation_scale == base_scale
            else aggregate_to_base(driver_df, base_scale)
        )
        if base_df.empty:
            continue
        local_specs = [
            spec
            for spec in indicator_specs
            if getattr(spec, "ticker", None) == ticker
            and getattr(spec, "kind", None) != "renko"
        ]
        engine = IndicatorEngine(local_specs)
        engine.fit(base_df)
        per_driver_df[ticker] = driver_df
        per_base_df[ticker] = base_df
        per_engine[ticker] = engine
        per_engine_ind_subs[ticker] = local_specs
    if not per_driver_df:
        raise ValueError("No OHLC rows returned for simulation")

    primary_ticker = tickers[0] if tickers[0] in per_base_df else next(iter(per_base_df))
    start_i, end_i = _simulation_row_range(per_base_df[primary_ticker], start_d, end_d)
    events: list[dict] = []
    renko_bricks: dict[str, list[tuple[int, float, float, str, float]]] = {}

    if not multi_ticker:
        driver_df = per_driver_df[primary_ticker]
        base_df = per_base_df[primary_ticker]
        ticker_subs, indicator_subs, renko_subs = compile_subscriptions(
            startup, base_scale, simulation_scale
        )
        for step in iter_simulation_steps(
            driver_df=driver_df,
            base_df=base_df,
            base_scale=base_scale,
            simulation_scale=simulation_scale,
            ticker_subs=ticker_subs,
            indicator_subs=indicator_subs,
            indicator_engine=per_engine[primary_ticker],
            renko_subs=renko_subs,
        ):
            if not (start_i <= step.base_row <= end_i):
                continue
            lines = (
                list(expand_step_to_lines(step, portfolio_provider=_empty_portfolio))
                if step.fired
                else []
            )
            base_unix = int(pd.Timestamp(step.base_ts).timestamp())
            chart_time = _time_for_chart(base_unix, base_scale)
            marks = {primary_ticker: float(step.running.close)}
            for line_index, line in enumerate(lines):
                points = line.points[1:]
                for point in points:
                    if isinstance(point, InputRenkoDataPoint):
                        renko_bricks.setdefault(str(point.id), []).append(
                            (
                                int(line.unixtime),
                                float(point.open),
                                float(point.close),
                                str(point.direction),
                                float(point.brick_size),
                            )
                        )
                is_last = line_index == len(lines) - 1
                events.append(
                    _event(
                        unixtime=line.unixtime,
                        points=points,
                        fills=marks,
                        marks=marks,
                        invoke_strategy=True,
                        record_equity=is_last,
                        base_close=is_last and step.is_base_close,
                        chart_time=chart_time,
                        benchmark_close=(step.running.close if is_last and step.is_base_close else None),
                        base_row=step.base_row,
                    )
                )
            if not lines:
                events.append(
                    _event(
                        unixtime=step.unixtime,
                        points=[],
                        fills=marks,
                        marks=marks,
                        invoke_strategy=False,
                        record_equity=True,
                        base_close=step.is_base_close,
                        chart_time=chart_time,
                        benchmark_close=(step.running.close if step.is_base_close else None),
                        base_row=step.base_row,
                    )
                )
        total_units = max(0, end_i - start_i + 1)
    else:
        ticker_sub_order = [
            point for point in startup.root if isinstance(point, OutputTickerSubscription)
        ]
        indicator_sub_order = [
            point.indicator
            for point in startup.root
            if isinstance(point, OutputIndicatorSubscriptionOrder)
        ]
        ts_to_row: dict[str, dict[pd.Timestamp, int]] = {}
        for ticker, frame in per_base_df.items():
            rows: dict[pd.Timestamp, int] = {}
            for index, timestamp in enumerate(frame.index):
                timestamp = pd.Timestamp(timestamp)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.tz_localize("UTC")
                rows[timestamp] = index
            ts_to_row[ticker] = rows
        timeline = sorted({timestamp for rows in ts_to_row.values() for timestamp in rows})
        primary_rows = ts_to_row[primary_ticker]
        start_ts = pd.Timestamp(start_d).tz_localize("UTC")
        end_exclusive = pd.Timestamp(end_d).tz_localize("UTC") + pd.Timedelta(days=1)
        last_prices: dict[str, float] = {}
        total_units = 0
        for timestamp in timeline:
            if not (start_ts <= timestamp < end_exclusive):
                continue
            unixtime = int(timestamp.timestamp())
            points: list = []
            fills: dict[str, float] = {}
            primary_close: float | None = None
            for subscription in ticker_sub_order:
                row = ts_to_row.get(subscription.ticker, {}).get(timestamp)
                if row is None:
                    continue
                frame = per_base_df[subscription.ticker]
                from strategies_v2.utils import InputOhlcDataPoint, Ohlc

                close = float(frame.iloc[row]["close"])
                points.append(
                    InputOhlcDataPoint(
                        id=str(subscription.id),
                        ticker=subscription.ticker,
                        ohlc=Ohlc(
                            open=float(frame.iloc[row]["open"]),
                            high=float(frame.iloc[row]["high"]),
                            low=float(frame.iloc[row]["low"]),
                            close=close,
                            volume=float(frame.iloc[row].get("volume", 0.0)),
                        ),
                        closed=True,
                    )
                )
                fills[subscription.ticker] = close
                last_prices[subscription.ticker] = close
                if subscription.ticker == primary_ticker:
                    primary_close = close
            for subscription in indicator_sub_order:
                ticker = getattr(subscription, "ticker", None)
                row = ts_to_row.get(ticker, {}).get(timestamp) if ticker else None
                if row is None:
                    continue
                local_specs = per_engine_ind_subs.get(ticker, [])
                try:
                    local_index = local_specs.index(subscription)
                except ValueError:
                    continue
                for point in per_engine[ticker].values_at_row_for_subscription(local_index, row):
                    points.append(point.model_copy(update={"id": str(subscription.id)}))
            primary_row = primary_rows.get(timestamp)
            base_close = (
                primary_row is not None
                and primary_close is not None
                and start_i <= primary_row <= end_i
            )
            total_units += int(base_close)
            events.append(
                _event(
                    unixtime=unixtime,
                    points=points,
                    fills=fills,
                    marks=last_prices,
                    invoke_strategy=bool(points),
                    record_equity=bool(last_prices),
                    base_close=base_close,
                    chart_time=_time_for_chart(unixtime, base_scale),
                    benchmark_close=primary_close if base_close else None,
                    base_row=primary_row,
                    mark_before_input=True,
                )
            )

    renko_specs: list[RenkoIndicatorSubscription] = [
        point.indicator
        for point in startup.root
        if isinstance(point, OutputIndicatorSubscriptionOrder)
        and isinstance(point.indicator, RenkoIndicatorSubscription)
    ]
    charts = _build_subscription_charts(
        tickers=tickers,
        base_scale=base_scale,
        per_base_df=per_base_df,
        per_engine=per_engine,
        per_engine_ind_subs=per_engine_ind_subs,
        primary_ticker=primary_ticker,
        start_d=start_d,
        end_d=end_d,
        markers={},
        output_indicator_points={},
        renko_specs=renko_specs,
        renko_bricks=renko_bricks,
    )
    return {
        "strategy_name": _read_strategy_name(workspace),
        "tickers": tickers,
        "base_scale": base_scale,
        "simulation_scale": simulation_scale,
        "primary_ticker": primary_ticker,
        "initial_deposit": initial_deposit,
        "max_leverage": max_leverage,
        "multi_ticker": multi_ticker,
        "total_units": total_units,
        "subscription_charts": [chart.model_dump(mode="json") for chart in charts],
        "events": events,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--startup")
    parser.add_argument("--study-startups")
    parser.add_argument("--raw-data")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    workspace = Path(args.workspace).resolve()
    if bool(args.startup) == bool(args.study_startups):
        parser.error("exactly one of --startup or --study-startups is required")
    try:
        if args.study_startups:
            raw_startups = json.loads(Path(args.study_startups).read_text(encoding="utf-8"))
            if not isinstance(raw_startups, list):
                raise ValueError("study startups must be a JSON array")
            startups = [StrategyOutput.model_validate(item) for item in raw_startups]
            payload = prepare_study(workspace, startups)
        else:
            startup = StrategyOutput.model_validate_json(
                Path(args.startup).read_text(encoding="utf-8")
            )
            query = None
            if args.raw_data:
                query = StudyBarsQuery(msgpack.unpackb(Path(args.raw_data).read_bytes(), raw=False))
            payload = prepare(workspace, startup, query=query)
    except RustOptimizerUnsupported as exc:
        print(f"RUST_OPTIMIZER_UNSUPPORTED: {exc}", file=sys.stderr)
        return 78
    Path(args.output).write_bytes(msgpack.packb(payload, use_bin_type=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
