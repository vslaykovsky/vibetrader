"""Shared driver loop for strategies_v2 simulation.

Handles the case where the simulator steps at a finer bar resolution (``simulation_scale``)
than the strategy's base ``scale``: it aggregates driver bars into the base bar (running OHLC),
decides per-subscription which driver bars fire an intermediate update vs the final closed
update, and yields ``SimulationStep`` records that both the SSE simulator and the backtest
script consume the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterator, Sequence

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
    BollingerBandsIndicatorSubscription,
    EmaIndicatorSubscription,
    FibonacciIndicatorSubscription,
    InputIndicatorDataPoint,
    InputOhlcDataPoint,
    InputPortfolioDataPoint,
    InputRenkoDataPoint,
    MacdIndicatorSubscription,
    Ohlc,
    OutputIndicatorSubscriptionOrder,
    OutputTickerSubscription,
    RenkoIndicatorSubscription,
    RsiIndicatorSubscription,
    SmaIndicatorSubscription,
    StochasticIndicatorSubscription,
    StrategyInput,
    StrategyOutput,
)

_INDICATOR_SUBS = (
    SmaIndicatorSubscription,
    EmaIndicatorSubscription,
    MacdIndicatorSubscription,
    RsiIndicatorSubscription,
    AtrIndicatorSubscription,
    BollingerBandsIndicatorSubscription,
    StochasticIndicatorSubscription,
    FibonacciIndicatorSubscription,
)


@dataclass
class SubscriptionSpec:
    """Compiled view of a subscription: effective ``update_scale`` + reference to the source model.

    ``id`` is the resolved, non-empty, host-unique handle (user-provided when the strategy set
    one, auto-assigned by ``assign_subscription_ids`` otherwise) — it is echoed on every input
    point produced by this subscription.
    """

    id: str
    ticker: str
    scale: str
    update_scale: str
    source: object
    indicator_name: str | None = None


def assign_subscription_ids(startup: StrategyOutput) -> StrategyOutput:
    """Return a copy of ``startup`` where every subscription has a non-empty, unique ``id``.

    User-provided ids are preserved (and validated unique across all subs). Subscriptions
    submitted without an ``id`` are auto-assigned a deterministic ``f"{kind}_{n}"`` handle,
    where ``n`` is the smallest non-negative integer that does not collide with another id
    in the same startup batch. The returned object can be passed everywhere downstream
    (compile_subscriptions, IndicatorEngine, multi-ticker walker) — every consumer can rely on
    ``sub.id`` being a non-empty string.
    """
    used: set[str] = set()
    for p in startup.root:
        sub = _subscription_of(p)
        if sub is None:
            continue
        sid = getattr(sub, "id", None)
        if sid:
            if sid in used:
                raise ValueError(f"duplicate subscription id: {sid!r}")
            used.add(sid)

    new_root: list = []
    for p in startup.root:
        sub = _subscription_of(p)
        if sub is None:
            new_root.append(p)
            continue
        sid = getattr(sub, "id", None)
        if not sid:
            sid = _auto_id(sub.kind, used)
            used.add(sid)
            sub_with_id = sub.model_copy(update={"id": sid})
            if isinstance(p, OutputTickerSubscription):
                new_root.append(sub_with_id)
            else:
                new_root.append(p.model_copy(update={"indicator": sub_with_id}))
        else:
            new_root.append(p)
    return StrategyOutput(new_root)


def _subscription_of(p) -> object | None:
    if isinstance(p, OutputTickerSubscription):
        return p
    if isinstance(p, OutputIndicatorSubscriptionOrder):
        return p.indicator
    return None


def _auto_id(kind: str, used: set[str]) -> str:
    n = 0
    while True:
        candidate = f"{kind}_{n}"
        if candidate not in used:
            return candidate
        n += 1


@dataclass
class RunningBar:
    open: float
    high: float
    low: float
    close: float


@dataclass
class RenkoState:
    """Per-subscription running anchor for close-based Renko brick generation."""

    anchor: float | None = None


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
    renko_points: list[InputRenkoDataPoint] = field(default_factory=list)
    """Renko bricks produced on this driver bar, in strict formation order."""
    partial_snapshot: list[InputOhlcDataPoint | InputIndicatorDataPoint] = field(
        default_factory=list
    )
    """Snapshot of every partial=True ticker/indicator subscription's current running value.

    Populated only when ``renko_points`` is non-empty; carried on each renko line so the strategy
    sees current partial state alongside each brick event.
    """
    is_base_close: bool = False
    """True iff this driver bar ends the current base-scale bar."""
    next_driver_unixtime: int | None = None
    """Unixtime of the next driver bar, if any; used to bound per-event line nudging."""
    fired: bool = False
    """True iff any ticker / indicator / renko subscription fires on this driver bar."""


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
) -> tuple[list[SubscriptionSpec], list[SubscriptionSpec], list[SubscriptionSpec]]:
    """Split startup subs into ``(ticker_specs, indicator_specs, renko_specs)``.

    ``partial=False`` (default) forces the effective ``update_scale`` to the subscription's own
    ``scale`` so only ``closed=True`` points fire for that subscription, regardless of any
    ``update_scale`` set on the model. ``partial=True`` uses the subscription's ``update_scale``
    (defaulting to ``simulation_scale`` when unset) so the strategy additionally sees
    ``closed=False`` partial points at that cadence.

    Subscription ids are resolved up front via ``assign_subscription_ids`` so every returned
    ``SubscriptionSpec`` has a stable, unique ``id`` to echo on emitted points. Renko
    subscriptions are returned in a dedicated list because they produce their own input
    kind (``InputRenkoDataPoint``) and are tracked by per-subscription brick state, not by the
    pandas-backed ``IndicatorEngine``.
    """
    startup = assign_subscription_ids(startup)
    base_scale = normalize_scale(base_scale)
    simulation_scale = normalize_scale(simulation_scale)
    tickers: list[SubscriptionSpec] = []
    indicators: list[SubscriptionSpec] = []
    renkos: list[SubscriptionSpec] = []
    for p in startup.root:
        if isinstance(p, OutputTickerSubscription):
            us = _effective_update_scale(
                p.update_scale, p.scale, base_scale, simulation_scale, p.partial
            )
            tickers.append(
                SubscriptionSpec(
                    id=str(p.id),
                    ticker=p.ticker,
                    scale=p.scale,
                    update_scale=us,
                    source=p,
                )
            )
        elif isinstance(p, OutputIndicatorSubscriptionOrder):
            ind = p.indicator
            if isinstance(ind, RenkoIndicatorSubscription):
                us = _effective_update_scale(
                    ind.update_scale, ind.scale, base_scale, simulation_scale, ind.partial
                )
                renkos.append(
                    SubscriptionSpec(
                        id=str(ind.id),
                        ticker=ind.ticker,
                        scale=ind.scale,
                        update_scale=us,
                        source=ind,
                        indicator_name=ind.kind,
                    )
                )
            elif isinstance(ind, _INDICATOR_SUBS):
                us = _effective_update_scale(
                    ind.update_scale, ind.scale, base_scale, simulation_scale, ind.partial
                )
                indicators.append(
                    SubscriptionSpec(
                        id=str(ind.id),
                        ticker=ind.ticker,
                        scale=ind.scale,
                        update_scale=us,
                        source=ind,
                        indicator_name=ind.kind,
                    )
                )
    return tickers, indicators, renkos


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
    renko_subs: Sequence[SubscriptionSpec] = (),
) -> Iterator[SimulationStep]:
    """Yield one ``SimulationStep`` per driver bar, building the per-step input points.

    Steps where no subscription fires still yield (``fired=False``) so callers can advance clocks
    or pacing without calling the strategy. Renko subscriptions are stateful: ``renko_subs`` is
    scanned on every firing driver bar and produces 0..n ``InputRenkoDataPoint`` entries in
    ``step.renko_points``. When bricks are produced, ``step.partial_snapshot`` is populated with
    the current running values of every ``partial=True`` ticker/indicator subscription, so a
    downstream caller can ride those alongside each brick on its own ``StrategyInput`` line.
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
    renko_states: list[RenkoState] = [RenkoState() for _ in renko_subs]

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
                        ),
                        closed=is_base_close,
                    )
                )

        for ind_i, ind_spec in enumerate(indicator_subs):
            if not _fires_on(driver_ts, next_ts, ind_spec.update_scale):
                continue
            sub_index = ind_i
            if is_base_close:
                for pt in indicator_engine.values_at_row_for_subscription(
                    sub_index, base_row
                ):
                    step.indicator_points.append(
                        pt.model_copy(
                            update={"id": ind_spec.id, "ticker": ind_spec.ticker}
                        )
                    )
            else:
                for pt in indicator_engine.partial_values_at_row_for_subscription(
                    sub_index,
                    base_row,
                    partial_close=running.close,
                    partial_high=running.high,
                    partial_low=running.low,
                ):
                    step.indicator_points.append(
                        pt.model_copy(
                            update={"id": ind_spec.id, "ticker": ind_spec.ticker}
                        )
                    )

        for ri, rspec in enumerate(renko_subs):
            if not _fires_on(driver_ts, next_ts, rspec.update_scale):
                continue
            src = rspec.source
            assert isinstance(src, RenkoIndicatorSubscription)
            brick_size = float(src.brick_size)
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
            step.partial_snapshot = _build_partial_snapshot(
                ticker_subs=ticker_subs,
                indicator_subs=indicator_subs,
                running=running,
                base_row=base_row,
                indicator_engine=indicator_engine,
            )

        step.fired = bool(
            step.ticker_points or step.indicator_points or step.renko_points
        )
        yield step


def _build_partial_snapshot(
    *,
    ticker_subs: Sequence[SubscriptionSpec],
    indicator_subs: Sequence[SubscriptionSpec],
    running: RunningBar,
    base_row: int,
    indicator_engine: IndicatorEngine,
) -> list[InputOhlcDataPoint | InputIndicatorDataPoint]:
    """Snapshot every ``partial=True`` ticker/indicator sub at the current running values.

    The result is used as a "here's the world right now" payload attached to sub-bar event lines
    (renko bricks today, ticks / limit fills / etc. later). Non-partial subs are excluded because
    their contract is "only fire at the subscription's own scale close".
    """
    out: list[InputOhlcDataPoint | InputIndicatorDataPoint] = []
    for sub in ticker_subs:
        src = sub.source
        if isinstance(src, OutputTickerSubscription) and src.partial:
            out.append(
                InputOhlcDataPoint(
                    id=sub.id,
                    ticker=src.ticker,
                    ohlc=Ohlc(
                        open=running.open,
                        high=running.high,
                        low=running.low,
                        close=running.close,
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
            out.append(pt.model_copy(update={"id": sub.id, "ticker": sub.ticker}))
    return out


def expand_step_to_lines(
    step: SimulationStep,
    *,
    portfolio_provider: Callable[[], InputPortfolioDataPoint],
) -> Iterator[StrategyInput]:
    """Fan a ``SimulationStep`` into one or more ``StrategyInput`` lines.

    - Regular ticker/indicator points go on a single line at ``step.unixtime`` (only emitted if
      any regular point fires).
    - Each renko brick becomes its own line with a nudged unixtime ``step.unixtime + k``, carrying
      the per-step ``partial_snapshot`` alongside the brick so the strategy sees current running
      values of every ``partial=True`` subscription when it processes the brick event.

    ``portfolio_provider`` is invoked once per line so that lines emitted after a trade from an
    earlier line reflect the updated portfolio (positions and ``deposit_ratio``). The generator
    raises ``ValueError`` if the nudged unixtimes would collide with the next driver bar.
    """
    regular_points: list = [*step.ticker_points, *step.indicator_points]
    base_t = step.unixtime
    max_t = (
        step.next_driver_unixtime - 1
        if step.next_driver_unixtime is not None
        else None
    )

    if regular_points:
        yield StrategyInput(
            unixtime=base_t,
            points=[portfolio_provider(), *regular_points],
        )
    brick_offset = 1 if regular_points else 0
    for i, brick in enumerate(step.renko_points):
        t = base_t + brick_offset + i
        if max_t is not None and t > max_t:
            raise ValueError(
                f"Cannot fit renko brick line at t={t} before next driver bar at "
                f"unixtime={step.next_driver_unixtime}; use a coarser simulation_scale or "
                f"a larger brick_size"
            )
        yield StrategyInput(
            unixtime=t,
            points=[portfolio_provider(), brick, *step.partial_snapshot],
        )
