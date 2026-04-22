from __future__ import annotations

from typing import Sequence

import pandas as pd

from application.services import indicator_series as ind
from strategies_v2.utils import (
    AtrIndicatorSubscription,
    EmaIndicatorSubscription,
    InputIndicatorDataPoint,
    MacdIndicatorSubscription,
    RsiIndicatorSubscription,
    SmaIndicatorSubscription,
)

Subscription = (
    SmaIndicatorSubscription
    | EmaIndicatorSubscription
    | MacdIndicatorSubscription
    | RsiIndicatorSubscription
    | AtrIndicatorSubscription
)


class IndicatorEngine:
    """Vector pandas indicators; ``fit`` then ``values_at_row`` in subscription order.

    For intermediate (partial) in-bar updates, call ``partial_values_at_row`` with the current
    running OHLC of the bar; the last row is overridden before recomputing the indicator so the
    emitted value reflects the partial bar.
    """

    def __init__(self, subscriptions: Sequence[Subscription]) -> None:
        self._subs: list[Subscription] = list(subscriptions)
        self._series: list[tuple[str, pd.Series]] = []
        self._close: pd.Series | None = None
        self._high: pd.Series | None = None
        self._low: pd.Series | None = None

    def fit(self, ohlc: pd.DataFrame) -> None:
        required = {"open", "high", "low", "close"}
        missing = required - set(ohlc.columns)
        if missing:
            raise ValueError(f"ohlc DataFrame missing columns: {sorted(missing)}")
        close = ohlc["close"].astype(float)
        high = ohlc["high"].astype(float)
        low = ohlc["low"].astype(float)
        self._close = close
        self._high = high
        self._low = low
        self._series.clear()
        for sub in self._subs:
            self._series.append(self._compute_series(sub, close, high, low))

    @staticmethod
    def _compute_series(
        sub: Subscription,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
    ) -> tuple[str, pd.Series]:
        if isinstance(sub, SmaIndicatorSubscription):
            return ("sma", ind.sma_series(close, sub.period))
        if isinstance(sub, EmaIndicatorSubscription):
            return ("ema", ind.ema_series(close, sub.period))
        if isinstance(sub, MacdIndicatorSubscription):
            return ("macd", ind.macd_line_series(close, sub.fast_period, sub.slow_period))
        if isinstance(sub, RsiIndicatorSubscription):
            return ("rsi", ind.rsi_series(close, sub.period))
        if isinstance(sub, AtrIndicatorSubscription):
            return ("atr", ind.atr_series(high, low, close, sub.period))
        raise TypeError(f"Unsupported subscription type: {type(sub)!r}")

    def values_at_row(self, row: int) -> list[InputIndicatorDataPoint]:
        out: list[InputIndicatorDataPoint] = []
        for name, s in self._series:
            if row < 0 or row >= len(s):
                continue
            v = s.iloc[row]
            if pd.isna(v):
                continue
            out.append(InputIndicatorDataPoint(name=name, value=float(v), closed=True))
        return out

    def value_at_row_for_subscription(
        self, subscription_index: int, row: int
    ) -> InputIndicatorDataPoint | None:
        if subscription_index < 0 or subscription_index >= len(self._series):
            return None
        name, s = self._series[subscription_index]
        if row < 0 or row >= len(s):
            return None
        v = s.iloc[row]
        if pd.isna(v):
            return None
        return InputIndicatorDataPoint(name=name, value=float(v), closed=True)

    def partial_values_at_row(
        self,
        row: int,
        *,
        partial_close: float,
        partial_high: float,
        partial_low: float,
    ) -> list[InputIndicatorDataPoint]:
        """Recompute indicators at ``row`` with the last bar overridden by the running partial OHLC."""
        if self._close is None or self._high is None or self._low is None:
            return []
        if row < 0 or row >= len(self._close):
            return []
        close = self._close.copy()
        high = self._high.copy()
        low = self._low.copy()
        close.iloc[row] = float(partial_close)
        high.iloc[row] = float(partial_high)
        low.iloc[row] = float(partial_low)
        out: list[InputIndicatorDataPoint] = []
        for sub in self._subs:
            name, s = self._compute_series(sub, close, high, low)
            v = s.iloc[row]
            if pd.isna(v):
                continue
            out.append(InputIndicatorDataPoint(name=name, value=float(v), closed=False))
        return out

    def partial_value_at_row_for_subscription(
        self,
        subscription_index: int,
        row: int,
        *,
        partial_close: float,
        partial_high: float,
        partial_low: float,
    ) -> InputIndicatorDataPoint | None:
        if self._close is None or self._high is None or self._low is None:
            return None
        if subscription_index < 0 or subscription_index >= len(self._subs):
            return None
        if row < 0 or row >= len(self._close):
            return None
        close = self._close.copy()
        high = self._high.copy()
        low = self._low.copy()
        close.iloc[row] = float(partial_close)
        high.iloc[row] = float(partial_high)
        low.iloc[row] = float(partial_low)
        sub = self._subs[subscription_index]
        name, s = self._compute_series(sub, close, high, low)
        v = s.iloc[row]
        if pd.isna(v):
            return None
        return InputIndicatorDataPoint(name=name, value=float(v), closed=False)

    @property
    def n_rows(self) -> int:
        if not self._series:
            return 0
        return int(self._series[0][1].shape[0])
