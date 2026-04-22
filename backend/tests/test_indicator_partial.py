import numpy as np
import pandas as pd

from application.services import indicator_series as ind
from application.services.indicators import IndicatorEngine
from strategies_v2.utils import SmaIndicatorSubscription


def test_indicator_engine_partial_values_override_last_close():
    rng = np.random.default_rng(0)
    n = 20
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, size=n))
    idx = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close},
        index=idx,
    )
    sub = SmaIndicatorSubscription(kind="sma", ticker="X", scale="1d", period=5)
    eng = IndicatorEngine([sub])
    eng.fit(df)
    closed_pt = eng.values_at_row(n - 1)[0]
    modified_close = float(close[-1] + 10.0)
    partial = eng.partial_values_at_row(
        n - 1,
        partial_close=modified_close,
        partial_high=modified_close + 1.0,
        partial_low=modified_close - 1.0,
    )[0]
    assert closed_pt.closed is True
    assert partial.closed is False
    expected = float(
        ind.sma_series(
            pd.Series(list(close[:-1]) + [modified_close]), sub.period
        ).iloc[-1]
    )
    assert partial.value == expected
    assert partial.value != closed_pt.value
