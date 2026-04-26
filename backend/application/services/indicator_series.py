"""Pandas-only indicator series; shared by ``IndicatorEngine`` and tests."""

from __future__ import annotations

import pandas as pd


def sma_series(close: pd.Series, period: int) -> pd.Series:
    return close.astype(float).rolling(window=period, min_periods=period).mean()


def ema_series(close: pd.Series, period: int) -> pd.Series:
    return close.astype(float).ewm(span=period, adjust=False).mean()


def macd_line_series(close: pd.Series, fast_period: int, slow_period: int) -> pd.Series:
    c = close.astype(float)
    fast = c.ewm(span=fast_period, adjust=False).mean()
    slow = c.ewm(span=slow_period, adjust=False).mean()
    return fast - slow


def macd_signal_series(
    close: pd.Series, fast_period: int, slow_period: int, signal_period: int
) -> pd.Series:
    line = macd_line_series(close, fast_period, slow_period)
    return line.ewm(
        span=signal_period, adjust=False, min_periods=signal_period
    ).mean()


def macd_histogram_series(
    close: pd.Series, fast_period: int, slow_period: int, signal_period: int
) -> pd.Series:
    line = macd_line_series(close, fast_period, slow_period)
    sig = macd_signal_series(close, fast_period, slow_period, signal_period)
    return line - sig


def rsi_series(close: pd.Series, period: int) -> pd.Series:
    """RSI with Wilder-style smoothing (``ewm(alpha=1/period)`` on gains/losses)."""
    c = close.astype(float)
    delta = c.diff()
    gain = delta.where(delta > 0.0, 0.0)
    loss = (-delta).where(delta < 0.0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    return 100.0 - (100.0 / (1.0 + rs))


def atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ATR (Wilder) on true range."""
    hi = high.astype(float)
    lo = low.astype(float)
    cl = close.astype(float)
    prev_close = cl.shift(1)
    tr = pd.concat(
        [
            hi - lo,
            (hi - prev_close).abs(),
            (lo - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def bollinger_bands_series(
    close: pd.Series, period: int, std_dev: float
) -> tuple[pd.Series, pd.Series, pd.Series]:
    c = close.astype(float)
    middle = c.rolling(window=period, min_periods=period).mean()
    std = c.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + float(std_dev) * std
    lower = middle - float(std_dev) * std
    return middle, upper, lower


def stochastic_k_d_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int,
    k_slowing: int,
    d_period: int,
) -> tuple[pd.Series, pd.Series]:
    hi = high.astype(float)
    lo = low.astype(float)
    cl = close.astype(float)
    lowest = lo.rolling(window=k_period, min_periods=k_period).min()
    highest = hi.rolling(window=k_period, min_periods=k_period).max()
    denom = (highest - lowest).replace(0.0, float("nan"))
    raw_k = 100.0 * (cl - lowest) / denom
    if k_slowing <= 1:
        slow_k = raw_k
    else:
        slow_k = raw_k.rolling(
            window=k_slowing, min_periods=k_slowing
        ).mean()
    d_line = slow_k.rolling(window=d_period, min_periods=d_period).mean()
    return slow_k, d_line


def fibonacci_retracement_level_series(
    high: pd.Series,
    low: pd.Series,
    *,
    lookback: int,
    level: float,
) -> pd.Series:
    hi = high.astype(float)
    lo = low.astype(float)
    hi_max = hi.rolling(window=lookback, min_periods=lookback).max()
    lo_min = lo.rolling(window=lookback, min_periods=lookback).min()
    span = (hi_max - lo_min).replace(0.0, float("nan"))
    return hi_max - float(level) * span
