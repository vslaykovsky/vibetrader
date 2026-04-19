from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from application.queries.historical_bars import HistoricalBarsQuery
from application.schemas.simulation_dto import StartSimulationCommand, simulation_event
from application.services.indicators import IndicatorEngine
from application.services.portfolio import Portfolio
from application.services.simulation_registry import SimulationRegistry
from application.services.simulation_session import SimulationSession
from application.services.speed_clock import ClockStopped, SpeedClock
from application.services.strategy_runtime import StrategyRuntime, StrategyRuntimeError
from strategies_v2.utils import (
    InputOhlcDataPoint,
    Ohlc,
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
                ticker, scale = _ticker_and_scale_from_startup(startup)
                ind_specs = _indicator_subscriptions_from_startup(startup)
                padding = _padding_days_for_indicator_subscriptions(ind_specs)
                df, _chunks = self._bars.fetch_chunked_merge(
                    ticker,
                    scale,
                    cmd.start_date,
                    cmd.end_date,
                    padding_days=padding,
                    provider=None,
                )
                if df.empty:
                    raise ValueError("No OHLC rows returned for simulation")
                start_i, end_i = _simulation_row_range(df, cmd.start_date, cmd.end_date)
                engine = IndicatorEngine(ind_specs)
                engine.fit(df)
                portfolio = Portfolio(initial_deposit=cmd.initial_deposit, ticker=ticker)
                sess.emit(simulation_event("status", status="running"))
                sess.emit(simulation_event("speed", bps=float(cmd.initial_speed_bps)))
                for i in range(len(df)):
                    if sess.stop_requested:
                        sess.emit(simulation_event("status", status="stopped"))
                        return
                    while not sess.pause.is_set():
                        if sess.stop_requested:
                            sess.emit(simulation_event("status", status="stopped"))
                            return
                        sess.pause.wait(timeout=0.05)
                    row = df.iloc[i]
                    unixtime = _row_unixtime(df.index[i])
                    close = float(row["close"])
                    ohlc = InputOhlcDataPoint(
                        ticker=ticker,
                        ohlc=Ohlc(
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=close,
                        ),
                    )
                    points: list = [portfolio.to_portfolio_datapoint(), ohlc]
                    points.extend(engine.values_at_row(i))
                    step = StrategyInput(unixtime=unixtime, points=points)
                    out = rt.send(step)
                    for item in out.root:
                        if isinstance(item, OutputMarketTradeOrder):
                            portfolio.apply_market_order(
                                direction=item.direction,
                                deposit_ratio=item.deposit_ratio,
                                price=close,
                                unixtime=unixtime,
                                reason="strategy",
                            )
                            sess.emit(
                                simulation_event(
                                    "trade",
                                    unixtime=unixtime,
                                    ticker=item.ticker,
                                    direction=item.direction,
                                    price=close,
                                    deposit_ratio=item.deposit_ratio,
                                    reason="strategy",
                                )
                            )
                    portfolio.record_equity(unixtime, close)
                    if start_i <= i <= end_i:
                        eq = portfolio.equity(close)
                        sess.emit(
                            simulation_event(
                                "bar",
                                unixtime=unixtime,
                                scale=scale,
                                ticker=ticker,
                                ohlc={
                                    "open": float(row["open"]),
                                    "high": float(row["high"]),
                                    "low": float(row["low"]),
                                    "close": close,
                                },
                            )
                        )
                        sess.emit(
                            simulation_event(
                                "pnl",
                                unixtime=unixtime,
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
