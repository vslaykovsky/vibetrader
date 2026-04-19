import numpy as np
import pandas as pd
import pytest

from application.services import indicator_series as ind
from application.services.indicators import IndicatorEngine
from strategies_v2.utils import (
    AtrIndicatorSubscription,
    EmaIndicatorSubscription,
    MacdIndicatorSubscription,
    RsiIndicatorSubscription,
    SmaIndicatorSubscription,
)


def _sample_ohlc(n: int = 60, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, size=n))
    noise = rng.normal(0, 0.2, size=n)
    high = close + np.abs(noise) + 0.1
    low = close - np.abs(noise) - 0.1
    open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 0.05, size=n)
    vol = rng.integers(1000, 5000, size=n).astype(float)
    idx = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def test_indicator_engine_sma_matches_series():
    df = _sample_ohlc(40)
    period = 7
    subs = [SmaIndicatorSubscription(kind="sma", ticker="X", scale="1d", period=period)]
    eng = IndicatorEngine(subs)
    eng.fit(df)
    ref = ind.sma_series(df["close"], period)
    for i in range(len(df)):
        pts = eng.values_at_row(i)
        rv = ref.iloc[i]
        if np.isnan(rv):
            assert pts == []
        else:
            assert len(pts) == 1
            assert pts[0].name == "sma"
            assert pts[0].value == pytest.approx(float(rv))


@pytest.mark.parametrize(
    "factory,ref_name",
    [
        (lambda: EmaIndicatorSubscription(kind="ema", ticker="X", scale="1d", period=9), "ema"),
        (lambda: RsiIndicatorSubscription(kind="rsi", ticker="X", scale="1d", period=14), "rsi"),
        (lambda: AtrIndicatorSubscription(kind="atr", ticker="X", scale="1d", period=10), "atr"),
    ],
)
def test_indicator_engine_matches_reference_series(factory, ref_name):
    df = _sample_ohlc(80)
    sub = factory()
    eng = IndicatorEngine([sub])
    eng.fit(df)
    if ref_name == "ema":
        ref = ind.ema_series(df["close"], sub.period)
    elif ref_name == "rsi":
        ref = ind.rsi_series(df["close"], sub.period)
    else:
        ref = ind.atr_series(df["high"], df["low"], df["close"], sub.period)
    for i in range(len(df)):
        pts = eng.values_at_row(i)
        rv = ref.iloc[i]
        if np.isnan(rv):
            assert pts == []
        else:
            assert len(pts) == 1
            assert pts[0].name == ref_name
            assert pts[0].value == pytest.approx(float(rv))


def test_indicator_engine_macd_line():
    df = _sample_ohlc(50)
    sub = MacdIndicatorSubscription(
        kind="macd", ticker="X", scale="1d", fast_period=8, slow_period=21, signal_period=5
    )
    eng = IndicatorEngine([sub])
    eng.fit(df)
    ref = ind.macd_line_series(df["close"], sub.fast_period, sub.slow_period)
    for i in range(len(df)):
        pts = eng.values_at_row(i)
        rv = ref.iloc[i]
        if np.isnan(rv):
            assert pts == []
        else:
            assert len(pts) == 1
            assert pts[0].name == "macd"
            assert pts[0].value == pytest.approx(float(rv))


def test_indicator_engine_subscription_order():
    df = _sample_ohlc(30)
    eng = IndicatorEngine(
        [
            SmaIndicatorSubscription(kind="sma", ticker="X", scale="1d", period=3),
            EmaIndicatorSubscription(kind="ema", ticker="X", scale="1d", period=3),
        ]
    )
    eng.fit(df)
    i = 10
    pts = eng.values_at_row(i)
    assert [p.name for p in pts] == ["sma", "ema"]


def test_indicator_engine_fit_requires_columns():
    eng = IndicatorEngine([SmaIndicatorSubscription(kind="sma", ticker="X", scale="1d", period=3)])
    bad = pd.DataFrame({"close": [1.0, 2.0]})
    with pytest.raises(ValueError, match="missing columns"):
        eng.fit(bad)
