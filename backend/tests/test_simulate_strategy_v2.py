from datetime import date

import pandas as pd

from scripts.simulate_strategy_v2 import _build_subscription_charts
from application.services import backtest_data as backtest_utils
from strategies_v2.utils import RenkoIndicatorSubscription


def test_build_subscription_charts_handles_atr_renko_bricks():
    idx = pd.date_range("2024-01-01", periods=2, freq="1D", tz="UTC")
    base_df = pd.DataFrame(
        {
            "open": [100.0, 102.0],
            "high": [103.0, 104.0],
            "low": [99.0, 101.0],
            "close": [102.0, 103.0],
            "volume": [1.0, 1.0],
        },
        index=idx,
    )
    brick_time = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp())
    marker = backtest_utils.LwcMarker(
        time="2024-01-01",
        position="belowBar",
        color="#26a69a",
        shape="arrowUp",
        text="BUY",
    )
    charts = _build_subscription_charts(
        tickers=["X"],
        base_scale="1d",
        per_base_df={"X": base_df},
        per_engine={},
        per_engine_ind_subs={},
        primary_ticker="X",
        start_d=date(2024, 1, 1),
        end_d=date(2024, 1, 2),
        markers={"X": [marker]},
        output_indicator_points={},
        renko_specs=[
            RenkoIndicatorSubscription(
                id="renko",
                ticker="X",
                scale="1d",
                brick_size_mode="atr",
                atr_period=2,
                atr_multiplier=1.5,
            )
        ],
        renko_bricks={"renko": [(brick_time, 100.0, 102.5, "up", 2.5)]},
    )

    assert [chart.title for chart in charts] == [
        "X price (1d)",
        "X renko bricks (ATR 2 x 1.5, scale=1d)",
    ]
    assert charts[1].series[0].data[0].open == 100.0
    assert charts[1].series[0].data[0].close == 102.5
    assert charts[0].series[0].markers == [marker]
