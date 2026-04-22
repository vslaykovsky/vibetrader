"""Shared driver loop for strategies_v2 simulation.

Handles the case where the simulator steps at a finer bar resolution (``simulation_scale``)
than the strategy's base ``scale``: it aggregates driver bars into the base bar (running OHLC),
decides per-subscription which driver bars fire an intermediate update vs the final closed
update, and yields ``SimulationStep`` records that both the SSE simulator and the backtest
script consume the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Sequence

import pandas as pd

from application.services.indicators import IndicatorEngine
from application.services.scale_utils import (
    floor_ts_to_scale,
    is_finer_or_equal,
    normalize_scale,
    scale_divides,
    scale_freq,
)
from strategies_v2.utils import (
    AtrIndicatorSubscription,
    EmaIndicatorSubscription,
    InputIndicatorDataPoint,
    InputOhlcDataPoint,
    MacdIndicatorSubscription,
    Ohlc,
    OutputIndicatorSubscriptionOrder,
    OutputTickerSubscription,
    RsiIndicatorSubscription,
    SmaIndicatorSubscription,
    StrategyOutput,
)

_INDICATOR_SUBS = (
    SmaIndicatorSubscription,
    EmaIndicatorSubscription,
    MacdIndicatorSubscription,
    RsiIndicatorSubscription,
    AtrIndicatorSubscription,
)


@dataclass
class SubscriptionSpec:
    """Compiled view of a subscription: effective ``update_scale`` + reference to the source model."""

    ticker: str
    scale: str
    update_scale: str
    source: object
    indicator_name: str | None = None


@dataclass
class RunningBar:
    open: float
    high: float
    low: float
    close: float


@dataclass
class SimulationStep:
    driver_index: int
    driver_ts: pd.Timestamp
    unixtime: int
    base_row: int
    """Row index in the aggregated base-scale DataFrame this driver bar belongs to."""
    base_ts: pd.Timestamp
    running: RunningBar
    """Running o/h/l/c for the current base bar (includes this driver bar)."""
    ticker_points: list[InputOhlcDataPoint] = field(default_factory=list)
    indicator_points: list[InputIndicatorDataPoint] = field(default_factory=list)
    is_base_close: bool = False
    """True iff this driver bar ends the current base-scale bar."""
    fired: bool = False
    """True iff any ticker / indicator subscription fires on this driver bar (strategy must be called)."""


def aggregate_to_base(driver_df: pd.DataFrame, base_scale: str) -> pd.DataFrame:
    """Aggregate driver OHLC rows into base-scale OHLC bars, UTC-anchored."""
    base_scale = normalize_scale(base_scale)
    if driver_df.empty:
        return driver_df.copy()
    idx = driver_df.index
    if getattr(idx, "tz", None) is None:
        driver_df = driver_df.copy()
        driver_df.index = driver_df.index.tz_localize("UTC")
    agg_map = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in driver_df.columns:
        agg_map["volume"] = "sum"
    base = (
        driver_df.resample(scale_freq(base_scale), label="left", closed="left")
        .agg(agg_map)
        .dropna(how="any", subset=["open", "high", "low", "close"])
    )
    return base


def compile_subscriptions(
    startup: StrategyOutput, base_scale: str, simulation_scale: str
) -> tuple[list[SubscriptionSpec], list[SubscriptionSpec]]:
    """Split startup subs into ``(ticker_specs, indicator_specs)`` with effective ``update_scale``.

    ``partial=False`` (default) forces the effective ``update_scale`` to the subscription's own
    ``scale`` so only ``closed=True`` points fire for that subscription, regardless of any
    ``update_scale`` set on the model. ``partial=True`` uses the subscription's ``update_scale``
    (defaulting to ``simulation_scale`` when unset) so the strategy additionally sees
    ``closed=False`` partial points at that cadence.
    """
    base_scale = normalize_scale(base_scale)
    simulation_scale = normalize_scale(simulation_scale)
    tickers: list[SubscriptionSpec] = []
    indicators: list[SubscriptionSpec] = []
    for p in startup.root:
        if isinstance(p, OutputTickerSubscription):
            us = _effective_update_scale(
                p.update_scale, p.scale, base_scale, simulation_scale, p.partial
            )
            tickers.append(
                SubscriptionSpec(
                    ticker=p.ticker, scale=p.scale, update_scale=us, source=p
                )
            )
        elif isinstance(p, OutputIndicatorSubscriptionOrder):
            ind = p.indicator
            if isinstance(ind, _INDICATOR_SUBS):
                us = _effective_update_scale(
                    ind.update_scale, ind.scale, base_scale, simulation_scale, ind.partial
                )
                indicators.append(
                    SubscriptionSpec(
                        ticker=ind.ticker,
                        scale=ind.scale,
                        update_scale=us,
                        source=ind,
                        indicator_name=ind.kind,
                    )
                )
    return tickers, indicators


def _effective_update_scale(
    update_scale: str | None,
    sub_scale: str,
    base_scale: str,
    simulation_scale: str,
    partial: bool,
) -> str:
    """Resolve the effective cadence at which a subscription fires.

    When ``partial`` is ``False`` the subscription only fires at its own ``scale`` (closed-only).
    When ``True`` the fire cadence is the provided ``update_scale`` (defaulting to
    ``simulation_scale``), clamped to ``[simulation_scale, sub_scale]`` and validated to divide
    the base scale.
    """
    sub_scale_n = normalize_scale(sub_scale)
    if not partial:
        return sub_scale_n
    if update_scale is None:
        us = simulation_scale
    else:
        us = normalize_scale(update_scale)
        if not is_finer_or_equal(us, sub_scale_n):
            raise ValueError(
                f"update_scale {us!r} must be at most as coarse as subscription scale {sub_scale_n!r}"
            )
    if not is_finer_or_equal(simulation_scale, us):
        us = simulation_scale
    if not scale_divides(simulation_scale, us):
        raise ValueError(
            f"simulation_scale {simulation_scale!r} must divide update_scale {us!r}"
        )
    if not scale_divides(us, base_scale):
        raise ValueError(
            f"update_scale {us!r} must divide base scale {base_scale!r}"
        )
    return us


def _fires_on(driver_ts: pd.Timestamp, next_ts: pd.Timestamp | None, update_scale: str) -> bool:
    """True iff ``driver_ts`` closes a ``update_scale`` window (next bar is in a later window)."""
    cur = floor_ts_to_scale(driver_ts, update_scale)
    if next_ts is None:
        return True
    nxt = floor_ts_to_scale(next_ts, update_scale)
    return nxt != cur


def iter_simulation_steps(
    *,
    driver_df: pd.DataFrame,
    base_df: pd.DataFrame,
    base_scale: str,
    simulation_scale: str,
    ticker_subs: Sequence[SubscriptionSpec],
    indicator_subs: Sequence[SubscriptionSpec],
    indicator_engine: IndicatorEngine,
) -> Iterator[SimulationStep]:
    """Yield one ``SimulationStep`` per driver bar, building the per-step input points.

    Steps where no subscription fires still yield (``fired=False``) so callers can advance clocks
    or pacing without calling the strategy.
    """
    if driver_df.empty:
        return
    base_scale = normalize_scale(base_scale)
    simulation_scale = normalize_scale(simulation_scale)
    base_index = base_df.index
    bucket_to_row: dict[pd.Timestamp, int] = {}
    for i in range(len(base_index)):
        b = floor_ts_to_scale(pd.Timestamp(base_index[i]), base_scale)
        bucket_to_row[b] = i
    cur_base_idx: int | None = None
    running: RunningBar | None = None

    total = len(driver_df)
    for j in range(total):
        driver_ts = pd.Timestamp(driver_df.index[j])
        if getattr(driver_ts, "tzinfo", None) is None:
            driver_ts = driver_ts.tz_localize("UTC")
        next_ts = (
            pd.Timestamp(driver_df.index[j + 1]) if j + 1 < total else None
        )
        if next_ts is not None and getattr(next_ts, "tzinfo", None) is None:
            next_ts = next_ts.tz_localize("UTC")
        base_ts = floor_ts_to_scale(driver_ts, base_scale)
        base_row = bucket_to_row.get(base_ts)
        if base_row is None:
            continue

        row = driver_df.iloc[j]
        o, h, l, c = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
        )
        if cur_base_idx != base_row or running is None:
            running = RunningBar(open=o, high=h, low=l, close=c)
            cur_base_idx = base_row
        else:
            if h > running.high:
                running.high = h
            if l < running.low:
                running.low = l
            running.close = c

        is_base_close = _fires_on(driver_ts, next_ts, base_scale)

        step = SimulationStep(
            driver_index=j,
            driver_ts=driver_ts,
            unixtime=int(driver_ts.timestamp()),
            base_row=base_row,
            base_ts=base_ts,
            running=RunningBar(**running.__dict__),
            is_base_close=is_base_close,
        )

        for ts in ticker_subs:
            if _fires_on(driver_ts, next_ts, ts.update_scale):
                step.ticker_points.append(
                    InputOhlcDataPoint(
                        ticker=ts.ticker,
                        ohlc=Ohlc(
                            open=running.open,
                            high=running.high,
                            low=running.low,
                            close=running.close,
                        ),
                        closed=is_base_close,
                    )
                )

        for ind_i, ind_spec in enumerate(indicator_subs):
            if not _fires_on(driver_ts, next_ts, ind_spec.update_scale):
                continue
            sub_index = ind_i
            if is_base_close:
                pt = indicator_engine.value_at_row_for_subscription(sub_index, base_row)
                if pt is not None:
                    step.indicator_points.append(pt)
            else:
                pt = indicator_engine.partial_value_at_row_for_subscription(
                    sub_index,
                    base_row,
                    partial_close=running.close,
                    partial_high=running.high,
                    partial_low=running.low,
                )
                if pt is not None:
                    step.indicator_points.append(pt)

        step.fired = bool(step.ticker_points or step.indicator_points)
        yield step
