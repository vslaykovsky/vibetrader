import numpy as np
import pandas as pd
import pytest

from application.services import indicator_series as ind
from application.services.indicators import IndicatorEngine
from strategies_v2.utils import (
    AtrIndicatorSubscription,
    BollingerBandsIndicatorSubscription,
    EmaIndicatorSubscription,
    FibonacciIndicatorSubscription,
    MacdIndicatorSubscription,
    RsiIndicatorSubscription,
    SmaIndicatorSubscription,
    StochasticIndicatorSubscription,
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


def test_bollinger_bands_series():
    close = pd.Series([1.0, 2.0, 3.0], dtype=float)
    mid, up, lo = ind.bollinger_bands_series(close, period=2, std_dev=2.0)
    assert float(mid.iloc[1]) == pytest.approx(1.5)
    assert float(up.iloc[1]) == pytest.approx(2.5)
    assert float(lo.iloc[1]) == pytest.approx(0.5)


def test_stochastic_k_d_series():
    high = pd.Series([10.0, 11.0, 12.0], dtype=float)
    low = pd.Series([9.0, 10.0, 10.0], dtype=float)
    cl = pd.Series([9.5, 10.5, 11.5], dtype=float)
    k_s, d_s = ind.stochastic_k_d_series(high, low, cl, k_period=2, k_slowing=1, d_period=2)
    assert float(k_s.iloc[2]) == pytest.approx(75.0)
    assert float(d_s.iloc[2]) == pytest.approx(75.0)


def test_indicator_engine_bollinger_bands_three_names():
    df = _sample_ohlc(40)
    sub = BollingerBandsIndicatorSubscription(
        kind="bb", ticker="X", scale="1d", period=5, std_dev=2.0
    )
    eng = IndicatorEngine([sub])
    eng.fit(df)
    i = 20
    pts = eng.values_at_row_for_subscription(0, i)
    names = sorted(p.name for p in pts)
    assert names == ["bb_lower", "bb_middle", "bb_upper"]
    mid_ref = ind.sma_series(df["close"], 5).iloc[i]
    by_n = {p.name: p.value for p in pts}
    assert by_n["bb_middle"] == pytest.approx(float(mid_ref))


def test_indicator_engine_stochastic_two_names():
    df = _sample_ohlc(60)
    sub = StochasticIndicatorSubscription(
        kind="stochastic",
        ticker="X",
        scale="1d",
        k_period=5,
        k_slowing=1,
        d_period=3,
    )
    eng = IndicatorEngine([sub])
    eng.fit(df)
    i = 30
    pts = eng.values_at_row_for_subscription(0, i)
    assert sorted(p.name for p in pts) == ["stoch_d", "stoch_k"]
    k_ref, d_ref = ind.stochastic_k_d_series(
        df["high"], df["low"], df["close"], 5, 1, 3
    )
    by_n = {p.name: p.value for p in pts}
    assert by_n["stoch_k"] == pytest.approx(float(k_ref.iloc[i]))
    assert by_n["stoch_d"] == pytest.approx(float(d_ref.iloc[i]))


def test_fibonacci_retracement_level_series():
    high = pd.Series([3.0, 5.0, 4.0, 6.0], dtype=float)
    low = pd.Series([2.0, 4.0, 3.0, 5.0], dtype=float)
    s = ind.fibonacci_retracement_level_series(high, low, lookback=3, level=0.5)
    assert float(s.iloc[2]) == pytest.approx(3.5)
    assert float(s.iloc[3]) == pytest.approx(4.5)


def test_indicator_engine_fibonacci_levels():
    df = pd.DataFrame(
        {
            "open": [1.0, 2.0, 3.0, 4.0],
            "high": [3.0, 5.0, 4.0, 6.0],
            "low": [2.0, 4.0, 3.0, 5.0],
            "close": [2.5, 4.5, 3.5, 5.5],
        },
        index=pd.date_range("2024-01-01", periods=4, freq="1D", tz="UTC"),
    )
    levels = [0.382, 0.618]
    sub = FibonacciIndicatorSubscription(
        ticker="X", scale="1d", lookback=3, levels=levels
    )
    eng = IndicatorEngine([sub])
    eng.fit(df)
    refs = {
        lv: ind.fibonacci_retracement_level_series(
            df["high"], df["low"], lookback=sub.lookback, level=float(lv)
        )
        for lv in levels
    }
    for i in range(len(df)):
        pts = eng.values_at_row_for_subscription(0, i)
        by_n = {p.name: p.value for p in pts}
        for lv in levels:
            name = "fib_" + str(float(lv)).replace(".", "p")
            rv = refs[lv].iloc[i]
            if np.isnan(rv):
                assert name not in by_n
            else:
                assert by_n[name] == pytest.approx(float(rv))
