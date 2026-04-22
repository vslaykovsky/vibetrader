from __future__ import annotations

import argparse
import json
import logging
import sys
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
    compile_subscriptions,
    iter_simulation_steps,
)
from application.services.strategy_runtime import StrategyRuntime
from application.use_cases.strategy_simulate import (
    _indicator_subscriptions_from_startup,
    _padding_days_for_indicator_subscriptions,
    _read_simulation_scale,
    _simulation_row_range,
)
from strategies import utils as backtest_utils
from strategies_v2.utils import (
    InputPortfolioDataPoint,
    OutputMarketTradeOrder,
    OutputTickerSubscription,
    StrategyInput,
    StrategyOutput,
)

logger = logging.getLogger(__name__)

_DAILY_SCALES = {"1d", "1w"}


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


def _clock_ticker_and_base_scale(startup: StrategyOutput) -> tuple[str, str]:
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
    if len(tickers) != 1:
        raise ValueError(
            "simulate_strategy_v2 supports a single clock symbol; "
            f"ticker_subscription listed {tickers!r}"
        )
    return tickers[0], rows[0][1]


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
    try:
        startup = rt.start(
            initial_input=StrategyInput(
                unixtime=0,
                points=[InputPortfolioDataPoint(positions=[])],
            )
        )
        logger.info(
            "subscriptions_from_strategy %s",
            json.dumps(startup.model_dump(mode="json")),
        )
        ticker, base_scale = _clock_ticker_and_base_scale(startup)
        logger.info(
            "startup workspace=%s entry_script=%s ticker=%s base_scale=%s",
            workspace,
            entry_script,
            ticker,
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
        for spec in ind_specs:
            st = getattr(spec, "ticker", None)
            if isinstance(st, str) and st.strip() and st.strip() != ticker:
                raise ValueError(
                    f"Indicator subscription ticker {st!r} must match clock ticker {ticker!r}"
                )
        padding = _padding_days_for_indicator_subscriptions(ind_specs)
        logger.info(
            "fetch bars ticker=%s sim_scale=%s start=%s end=%s padding_days=%s provider=%s",
            ticker,
            sim_scale,
            start_d,
            end_d,
            padding,
            provider,
        )
        driver_df, _ = bars_query.fetch_chunked_merge(
            ticker, sim_scale, start_d, end_d, padding_days=padding, provider=provider
        )
        if driver_df.empty:
            logger.error(
                "no OHLC rows ticker=%s sim_scale=%s start=%s end=%s padding_days=%s provider=%s",
                ticker,
                sim_scale,
                start_d,
                end_d,
                padding,
                provider,
            )
            raise ValueError("No OHLC rows returned for simulation")
        logger.info("driver_df rows=%s columns=%s", len(driver_df), list(driver_df.columns))
        base_df = (
            driver_df if sim_scale == base_scale else aggregate_to_base(driver_df, base_scale)
        )
        if base_df.empty:
            logger.error(
                "base_df empty after aggregation sim_scale=%s base_scale=%s driver_rows=%s",
                sim_scale,
                base_scale,
                len(driver_df),
            )
            raise ValueError("No base-scale bars after aggregation")
        start_i, end_i = _simulation_row_range(base_df, start_d, end_d)
        logger.info(
            "base_df rows=%s base_scale=%s simulation_row_range=[%s,%s] requested=[%s,%s]",
            len(base_df),
            base_scale,
            start_i,
            end_i,
            start_d,
            end_d,
        )
        engine = IndicatorEngine(ind_specs)
        engine.fit(base_df)
        ticker_subs, indicator_subs = compile_subscriptions(startup, base_scale, sim_scale)
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
        portfolio = Portfolio(initial_deposit=initial_deposit, ticker=ticker)

        price_points: list[backtest_utils.LwcCandlestickPoint] = []
        markers: list[backtest_utils.LwcMarker] = []
        equity_points: list[backtest_utils.LwcTimeValuePoint] = []
        bench_points: list[backtest_utils.LwcTimeValuePoint] = []
        table_rows: list[dict] = []
        bench_first_close: float | None = None

        for step in iter_simulation_steps(
            driver_df=driver_df,
            base_df=base_df,
            base_scale=base_scale,
            simulation_scale=sim_scale,
            ticker_subs=ticker_subs,
            indicator_subs=indicator_subs,
            indicator_engine=engine,
        ):
            fill_price = step.running.close
            pre_trade_count = len(portfolio.trades)
            if step.fired:
                points: list = [portfolio.to_portfolio_datapoint()]
                points.extend(step.ticker_points)
                points.extend(step.indicator_points)
                step_input = StrategyInput(unixtime=step.unixtime, points=points)
                out = rt.send(step_input)
                for item in out.root:
                    if isinstance(item, OutputMarketTradeOrder):
                        portfolio.apply_market_order(
                            direction=item.direction,
                            deposit_ratio=item.deposit_ratio,
                            price=fill_price,
                            unixtime=step.unixtime,
                            reason="strategy",
                        )
            portfolio.record_equity(step.unixtime, fill_price)

            if step.is_base_close and start_i <= step.base_row <= end_i:
                base_unix = int(pd.Timestamp(step.base_ts).timestamp())
                t = _time_for_chart(base_unix, base_scale)
                price_points.append(
                    backtest_utils.LwcCandlestickPoint(
                        time=t,
                        open=float(step.running.open),
                        high=float(step.running.high),
                        low=float(step.running.low),
                        close=float(step.running.close),
                    )
                )
                eq = portfolio.equity(fill_price)
                equity_points.append(
                    backtest_utils.LwcTimeValuePoint(time=t, value=float(eq))
                )
                if bench_first_close is None:
                    bench_first_close = float(step.running.close)
                bench_val = initial_deposit * (float(step.running.close) / bench_first_close)
                bench_points.append(
                    backtest_utils.LwcTimeValuePoint(time=t, value=float(bench_val))
                )
                for trade in portfolio.trades[pre_trade_count:]:
                    is_buy = trade.direction == "buy"
                    markers.append(
                        backtest_utils.LwcMarker(
                            time=t,
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
    finally:
        rt.close()

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

    price_chart = backtest_utils.LightweightChartsChart(
        title=f"{ticker} price and signals",
        series=[
            backtest_utils.LwcCandlestickSeries(
                label=ticker,
                options={"upColor": "#26a69a", "downColor": "#ef5350"},
                data=price_points,
                markers=markers or None,
            )
        ],
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
                label=f"Buy & hold {ticker}",
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

    strategy_name = _read_strategy_name(workspace)
    logger.info(
        "done strategy_name=%s trades=%s total_return=%s max_drawdown=%s",
        strategy_name,
        metrics.num_trades,
        metrics.total_return,
        metrics.max_drawdown,
    )
    return backtest_utils.DataJson(
        strategy_name=strategy_name,
        charts=[price_chart, equity_chart, trades_chart],
        metrics=metrics,
    )


def _write_workspace_outputs(doc: backtest_utils.DataJson, workspace: Path) -> tuple[Path, Path]:
    serialized = doc.model_dump(mode="json", exclude_none=True)
    metrics = serialized.pop("metrics", None) or {}
    backtest_path = workspace / "backtest.json"
    metrics_path = workspace / "metrics.json"
    workspace.mkdir(parents=True, exist_ok=True)
    backtest_path.write_text(
        json.dumps(serialized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
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
    print(f"wrote {metrics_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
