"""Pandas-only indicator series; shared by ``IndicatorEngine`` and tests."""

from __future__ import annotations

import pandas as pd


def sma_series(close: pd.Series, period: int) -> pd.Series:
    return close.astype(float).rolling(window=period, min_periods=period).mean()


def ema_series(close: pd.Series, period: int) -> pd.Series:
    return close.astype(float).ewm(span=period, adjust=False).mean()


def macd_line_series(close: pd.Series, fast_period: int, slow_period: int) -> pd.Series:
    """MACD line (fast EMA − slow EMA). Signal/histogram not emitted to the strategy host in MVP."""
    c = close.astype(float)
    fast = c.ewm(span=fast_period, adjust=False).mean()
    slow = c.ewm(span=slow_period, adjust=False).mean()
    return fast - slow


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
