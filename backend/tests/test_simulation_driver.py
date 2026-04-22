import pandas as pd
import pytest

from application.services.indicators import IndicatorEngine
from application.services.simulation_driver import (
    RunningBar,
    SimulationStep,
    aggregate_to_base,
    compile_subscriptions,
    expand_step_to_lines,
    iter_simulation_steps,
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


def test_iter_simulation_steps_emits_partial_and_closed_updates():
    idx = pd.date_range("2024-01-01 00:00", periods=48, freq="1h", tz="UTC")
    driver = pd.DataFrame(
        {
            "open": [float(i) for i in range(48)],
            "high": [float(i) + 0.5 for i in range(48)],
            "low": [float(i) - 0.5 for i in range(48)],
            "close": [float(i) + 0.25 for i in range(48)],
        },
        index=idx,
    )
    base = aggregate_to_base(driver, "1d")
    startup = StrategyOutput(
        [
            OutputTickerSubscription(
                ticker="X", scale="1d", update_scale="4h", partial=True
            ),
            OutputIndicatorSubscriptionOrder(
                indicator=SmaIndicatorSubscription(ticker="X", scale="1d", period=1)
            ),
        ]
    )
    ticker_subs, ind_subs, renko_subs = compile_subscriptions(startup, "1d", "1h")
    eng = IndicatorEngine([s.source for s in ind_subs])
    eng.fit(base)
    steps = list(
        iter_simulation_steps(
            driver_df=driver,
            base_df=base,
            base_scale="1d",
            simulation_scale="1h",
            ticker_subs=ticker_subs,
            indicator_subs=ind_subs,
            indicator_engine=eng,
            renko_subs=renko_subs,
        )
    )
    assert len(steps) == 48
    closed_steps = [s for s in steps if s.is_base_close]
    assert len(closed_steps) == 2
    ticker_fired = [s for s in steps if s.ticker_points]
    assert len(ticker_fired) == 12
    partial_ticker = [s for s in ticker_fired if not s.ticker_points[0].closed]
    closed_ticker = [s for s in ticker_fired if s.ticker_points[0].closed]
    assert len(partial_ticker) == 10
    assert len(closed_ticker) == 2
    indicator_fired = [s for s in steps if s.indicator_points]
    assert len(indicator_fired) == 2
    assert all(p.closed for s in indicator_fired for p in s.indicator_points)
    day1_close = closed_ticker[0].ticker_points[0].ohlc.close
    assert day1_close == driver.iloc[23]["close"]


def test_iter_simulation_steps_partial_flag_controls_emission():
    idx = pd.date_range("2024-01-01 00:00", periods=48, freq="1h", tz="UTC")
    driver = pd.DataFrame(
        {
            "open": [float(i) for i in range(48)],
            "high": [float(i) + 0.5 for i in range(48)],
            "low": [float(i) - 0.5 for i in range(48)],
            "close": [float(i) + 0.25 for i in range(48)],
        },
        index=idx,
    )
    base = aggregate_to_base(driver, "1d")
    startup = StrategyOutput(
        [
            OutputTickerSubscription(
                ticker="X", scale="1d", update_scale="4h", partial=True
            ),
            OutputIndicatorSubscriptionOrder(
                indicator=SmaIndicatorSubscription(
                    ticker="X",
                    scale="1d",
                    period=1,
                    update_scale="4h",
                    partial=False,
                )
            ),
        ]
    )
    ticker_subs, ind_subs, renko_subs = compile_subscriptions(startup, "1d", "1h")
    assert ticker_subs[0].update_scale == "4h"
    assert ind_subs[0].update_scale == "1d"
    assert renko_subs == []
    eng = IndicatorEngine([s.source for s in ind_subs])
    eng.fit(base)
    steps = list(
        iter_simulation_steps(
            driver_df=driver,
            base_df=base,
            base_scale="1d",
            simulation_scale="1h",
            ticker_subs=ticker_subs,
            indicator_subs=ind_subs,
            indicator_engine=eng,
            renko_subs=renko_subs,
        )
    )
    ticker_fired = [s for s in steps if s.ticker_points]
    partial_ticker = [s for s in ticker_fired if not s.ticker_points[0].closed]
    closed_ticker = [s for s in ticker_fired if s.ticker_points[0].closed]
    assert len(partial_ticker) == 10
    assert len(closed_ticker) == 2
    indicator_fired = [s for s in steps if s.indicator_points]
    assert len(indicator_fired) == 2
    assert all(p.closed for s in indicator_fired for p in s.indicator_points)


def test_iter_simulation_steps_emits_multiple_renko_bricks_on_big_move():
    idx = pd.date_range("2024-01-01 00:00", periods=5, freq="1h", tz="UTC")
    driver = pd.DataFrame(
        {
            "open": [100.0, 100.5, 101.0, 108.0, 107.5],
            "high": [100.5, 101.0, 101.5, 108.5, 108.0],
            "low": [99.5, 100.0, 100.5, 100.9, 105.5],
            "close": [100.0, 101.0, 101.0, 108.0, 106.0],
        },
        index=idx,
    )
    base = aggregate_to_base(driver, "1d")
    startup = StrategyOutput(
        [
            OutputTickerSubscription(
                ticker="X", scale="1d", update_scale="1h", partial=True
            ),
            OutputIndicatorSubscriptionOrder(
                indicator=RenkoIndicatorSubscription(
                    ticker="X",
                    scale="1d",
                    brick_size=2.0,
                    update_scale="1h",
                    partial=True,
                )
            ),
        ]
    )
    ticker_subs, ind_subs, renko_subs = compile_subscriptions(startup, "1d", "1h")
    assert len(renko_subs) == 1
    eng = IndicatorEngine([s.source for s in ind_subs])
    eng.fit(base)
    steps = list(
        iter_simulation_steps(
            driver_df=driver,
            base_df=base,
            base_scale="1d",
            simulation_scale="1h",
            ticker_subs=ticker_subs,
            indicator_subs=ind_subs,
            indicator_engine=eng,
            renko_subs=renko_subs,
        )
    )
    brick_counts = [len(s.renko_points) for s in steps]
    assert brick_counts == [0, 0, 0, 4, 1]
    big_move = steps[3]
    up_bricks = big_move.renko_points
    assert all(b.direction == "up" for b in up_bricks)
    assert [b.open for b in up_bricks] == [100.0, 102.0, 104.0, 106.0]
    assert [b.close for b in up_bricks] == [102.0, 104.0, 106.0, 108.0]
    assert all(b.brick_size == 2.0 and b.closed for b in up_bricks)
    down_brick = steps[4].renko_points[0]
    assert down_brick.direction == "down"
    assert down_brick.open == 108.0
    assert down_brick.close == 106.0
    snap_kinds = [p.kind for p in big_move.partial_snapshot]
    assert snap_kinds == ["ohlc"]
    snap_ohlc = big_move.partial_snapshot[0]
    assert snap_ohlc.ticker == "X"
    assert snap_ohlc.closed is False
    assert snap_ohlc.ohlc.close == 108.0


def test_expand_step_to_lines_splits_renko_bricks_into_monotonic_lines():
    idx = pd.date_range("2024-01-01 00:00", periods=5, freq="1h", tz="UTC")
    driver = pd.DataFrame(
        {
            "open": [100.0, 100.5, 101.0, 108.0, 107.5],
            "high": [100.5, 101.0, 101.5, 108.5, 108.0],
            "low": [99.5, 100.0, 100.5, 100.9, 105.5],
            "close": [100.0, 101.0, 101.0, 108.0, 106.0],
        },
        index=idx,
    )
    base = aggregate_to_base(driver, "1d")
    startup = StrategyOutput(
        [
            OutputTickerSubscription(
                ticker="X", scale="1d", update_scale="1h", partial=True
            ),
            OutputIndicatorSubscriptionOrder(
                indicator=RenkoIndicatorSubscription(
                    ticker="X",
                    scale="1d",
                    brick_size=2.0,
                    update_scale="1h",
                    partial=True,
                )
            ),
        ]
    )
    ticker_subs, ind_subs, renko_subs = compile_subscriptions(startup, "1d", "1h")
    eng = IndicatorEngine([s.source for s in ind_subs])
    eng.fit(base)
    steps = list(
        iter_simulation_steps(
            driver_df=driver,
            base_df=base,
            base_scale="1d",
            simulation_scale="1h",
            ticker_subs=ticker_subs,
            indicator_subs=ind_subs,
            indicator_engine=eng,
            renko_subs=renko_subs,
        )
    )
    big_move = steps[3]
    lines = list(
        expand_step_to_lines(
            big_move,
            portfolio_provider=lambda: InputPortfolioDataPoint(positions=[]),
        )
    )
    assert len(lines) == 5
    times = [line.unixtime for line in lines]
    assert times == [
        big_move.unixtime,
        big_move.unixtime + 1,
        big_move.unixtime + 2,
        big_move.unixtime + 3,
        big_move.unixtime + 4,
    ]
    assert times[-1] < (big_move.next_driver_unixtime or 10**18)
    assert all(line.points[0].kind == "portfolio" for line in lines)
    regular = lines[0]
    assert [p.kind for p in regular.points[1:]] == ["ohlc"]
    assert regular.points[1].closed is False
    for brick_line in lines[1:]:
        kinds = [p.kind for p in brick_line.points[1:]]
        assert kinds[0] == "renko"
        assert "ohlc" in kinds[1:]
    steady_step = steps[1]
    steady_lines = list(
        expand_step_to_lines(
            steady_step,
            portfolio_provider=lambda: InputPortfolioDataPoint(positions=[]),
        )
    )
    assert len(steady_lines) == 1
    assert steady_lines[0].unixtime == steady_step.unixtime
    assert [p.kind for p in steady_lines[0].points] == ["portfolio", "ohlc"]


def test_expand_step_to_lines_raises_when_bricks_overflow_next_bar():
    step = SimulationStep(
        driver_index=0,
        driver_ts=pd.Timestamp("2024-01-01 00:00", tz="UTC"),
        unixtime=1_700_000_000,
        base_row=0,
        base_ts=pd.Timestamp("2024-01-01 00:00", tz="UTC"),
        running=RunningBar(open=100.0, high=108.0, low=100.0, close=108.0),
        is_base_close=False,
        next_driver_unixtime=1_700_000_002,
        fired=True,
    )
    step.renko_points = [
        InputRenkoDataPoint(
            ticker="X",
            brick_size=2.0,
            open=100.0 + 2.0 * i,
            close=102.0 + 2.0 * i,
            direction="up",
        )
        for i in range(4)
    ]
    with pytest.raises(ValueError, match="brick"):
        list(
            expand_step_to_lines(
                step,
                portfolio_provider=lambda: InputPortfolioDataPoint(positions=[]),
            )
        )
