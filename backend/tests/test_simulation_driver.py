import pandas as pd

from application.services.indicators import IndicatorEngine
from application.services.simulation_driver import (
    aggregate_to_base,
    compile_subscriptions,
    iter_simulation_steps,
)
from strategies_v2.utils import (
    OutputIndicatorSubscriptionOrder,
    OutputTickerSubscription,
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
    ticker_subs, ind_subs = compile_subscriptions(startup, "1d", "1h")
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
    ticker_subs, ind_subs = compile_subscriptions(startup, "1d", "1h")
    assert ticker_subs[0].update_scale == "4h"
    assert ind_subs[0].update_scale == "1d"
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
