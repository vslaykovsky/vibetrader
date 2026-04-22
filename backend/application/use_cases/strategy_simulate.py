from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from application.queries.historical_bars import HistoricalBarsQuery
from application.schemas.simulation_dto import StartSimulationCommand, simulation_event
from application.services.indicators import IndicatorEngine
from application.services.portfolio import Portfolio
from application.services.scale_utils import (
    is_finer_or_equal,
    normalize_scale,
    scale_divides,
)
from application.services.simulation_driver import (
    aggregate_to_base,
    compile_subscriptions,
    iter_simulation_steps,
)
from application.services.simulation_registry import SimulationRegistry
from application.services.simulation_session import SimulationSession
from application.services.speed_clock import ClockStopped, SpeedClock
from application.services.strategy_runtime import StrategyRuntime, StrategyRuntimeError
from strategies_v2.utils import (
    OutputIndicatorSubscriptionOrder,
    OutputMarketTradeOrder,
    OutputTickerSubscription,
    StrategyInput,
    StrategyOutput,
)


class Pacing(Protocol):
    def wait_next(self) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None: ...

    def change_speed(self, bps: float) -> None: ...


def _default_pacing(bps: float) -> Pacing:
    return SpeedClock(bps)


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
    return max(30, min(500, max_bars))


def _ticker_and_scale_from_startup(startup: StrategyOutput) -> tuple[str, str]:
    for p in startup.root:
        if isinstance(p, OutputTickerSubscription):
            return p.ticker, p.scale
    raise ValueError("Strategy startup did not include ticker_subscription")


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


def _simulation_row_range(df: pd.DataFrame, start: date, end: date) -> tuple[int, int]:
    start_idx = -1
    end_idx = -1
    for i, ts in enumerate(df.index):
        d = pd.Timestamp(ts).date()
        if d >= start and start_idx < 0:
            start_idx = i
        if start_idx >= 0 and d <= end:
            end_idx = i
    if start_idx < 0 or end_idx < 0 or end_idx < start_idx:
        raise ValueError("No bars in requested simulation date range (after padding)")
    return start_idx, end_idx


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
        pacing_factory: Callable[[float], Pacing] | None = None,
    ) -> None:
        self._registry = registry
        self._bars = bars_query
        self._pacing_factory = pacing_factory or _default_pacing

    def pause(self, user_id: str, thread_id: str) -> None:
        sess = self._registry.get(user_id, thread_id)
        if sess is not None:
            sess.pause.clear()
            sess.emit(simulation_event("status", status="paused"))

    def resume(self, user_id: str, thread_id: str) -> None:
        sess = self._registry.get(user_id, thread_id)
        if sess is not None:
            sess.pause.set()
            sess.emit(simulation_event("status", status="running"))

    def change_speed(self, user_id: str, thread_id: str, bps: float) -> None:
        if bps <= 0:
            raise ValueError("bps must be positive")
        sess = self._registry.get(user_id, thread_id)
        if sess is not None:
            sess.set_speed_bps(bps)
            sess.emit(simulation_event("speed", bps=float(bps)))

    def stop(self, user_id: str, thread_id: str) -> None:
        self._registry.remove(user_id, thread_id)

    def start(self, cmd: StartSimulationCommand) -> None:
        workspace = cmd.strategy_workspace or (
            Path(__file__).resolve().parents[2] / "strategies_v2"
        )
        if cmd.strategy_workspace is None:
            candidate = workspace / cmd.thread_id
            if candidate.is_dir():
                workspace = candidate

        def run() -> None:
            pacing = self._pacing_factory(cmd.initial_speed_bps)
            rt: StrategyRuntime | None = None
            sess = self._registry.get(cmd.user_id, cmd.thread_id)
            if sess is None:
                return
            try:
                sess.emit(simulation_event("status", status="starting"))
                rt = StrategyRuntime(workspace, entry_script=cmd.strategy_entry)
                startup = rt.start()
                ticker, base_scale = _ticker_and_scale_from_startup(startup)
                base_scale = normalize_scale(base_scale)
                sim_scale = (
                    normalize_scale(cmd.simulation_scale)
                    if cmd.simulation_scale
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
                padding = _padding_days_for_indicator_subscriptions(ind_specs)
                driver_df, _chunks = self._bars.fetch_chunked_merge(
                    ticker,
                    sim_scale,
                    cmd.start_date,
                    cmd.end_date,
                    padding_days=padding,
                    provider=None,
                )
                if driver_df.empty:
                    raise ValueError("No OHLC rows returned for simulation")
                base_df = (
                    driver_df
                    if sim_scale == base_scale
                    else aggregate_to_base(driver_df, base_scale)
                )
                if base_df.empty:
                    raise ValueError("No base-scale bars after aggregation")
                start_i, end_i = _simulation_row_range(base_df, cmd.start_date, cmd.end_date)
                engine = IndicatorEngine(ind_specs)
                engine.fit(base_df)
                ticker_subs, indicator_subs = compile_subscriptions(
                    startup, base_scale, sim_scale
                )
                portfolio = Portfolio(initial_deposit=cmd.initial_deposit, ticker=ticker)
                sess.emit(simulation_event("status", status="running"))
                sess.emit(simulation_event("speed", bps=float(cmd.initial_speed_bps)))

                for step in iter_simulation_steps(
                    driver_df=driver_df,
                    base_df=base_df,
                    base_scale=base_scale,
                    simulation_scale=sim_scale,
                    ticker_subs=ticker_subs,
                    indicator_subs=indicator_subs,
                    indicator_engine=engine,
                ):
                    if sess.stop_requested:
                        sess.emit(simulation_event("status", status="stopped"))
                        return
                    while not sess.pause.is_set():
                        if sess.stop_requested:
                            sess.emit(simulation_event("status", status="stopped"))
                            return
                        sess.pause.wait(timeout=0.05)

                    fill_price = step.running.close
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
                                sess.emit(
                                    simulation_event(
                                        "trade",
                                        unixtime=step.unixtime,
                                        ticker=item.ticker,
                                        direction=item.direction,
                                        price=fill_price,
                                        deposit_ratio=item.deposit_ratio,
                                        reason="strategy",
                                    )
                                )
                    portfolio.record_equity(step.unixtime, fill_price)

                    if step.is_base_close and start_i <= step.base_row <= end_i:
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
                        try:
                            pacing.change_speed(sess.get_speed_bps())
                            pacing.wait_next()
                        except ClockStopped:
                            sess.emit(simulation_event("status", status="stopped"))
                            return
            except (StrategyRuntimeError, ValueError) as exc:
                sess.emit(simulation_event("status", status="error", message=str(exc)))
            finally:
                if rt is not None:
                    rt.close()

        session = SimulationSession(
            user_id=cmd.user_id,
            thread_id=cmd.thread_id,
            run_fn=run,
            initial_speed_bps=cmd.initial_speed_bps,
        )
        self._registry.replace(cmd.user_id, cmd.thread_id, session)
        session.start()
