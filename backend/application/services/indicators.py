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
    """Vector pandas indicators; ``fit`` then ``values_at_row`` in subscription order."""

    def __init__(self, subscriptions: Sequence[Subscription]) -> None:
        self._subs: list[Subscription] = list(subscriptions)
        self._series: list[tuple[str, pd.Series]] = []

    def fit(self, ohlc: pd.DataFrame) -> None:
        required = {"open", "high", "low", "close"}
        missing = required - set(ohlc.columns)
        if missing:
            raise ValueError(f"ohlc DataFrame missing columns: {sorted(missing)}")
        close = ohlc["close"]
        high = ohlc["high"]
        low = ohlc["low"]
        self._series.clear()
        for sub in self._subs:
            if isinstance(sub, SmaIndicatorSubscription):
                self._series.append(("sma", ind.sma_series(close, sub.period)))
            elif isinstance(sub, EmaIndicatorSubscription):
                self._series.append(("ema", ind.ema_series(close, sub.period)))
            elif isinstance(sub, MacdIndicatorSubscription):
                self._series.append(
                    (
                        "macd",
                        ind.macd_line_series(close, sub.fast_period, sub.slow_period),
                    )
                )
            elif isinstance(sub, RsiIndicatorSubscription):
                self._series.append(("rsi", ind.rsi_series(close, sub.period)))
            elif isinstance(sub, AtrIndicatorSubscription):
                self._series.append(("atr", ind.atr_series(high, low, close, sub.period)))
            else:
                raise TypeError(f"Unsupported subscription type: {type(sub)!r}")

    def values_at_row(self, row: int) -> list[InputIndicatorDataPoint]:
        out: list[InputIndicatorDataPoint] = []
        for name, s in self._series:
            if row < 0 or row >= len(s):
                continue
            v = s.iloc[row]
            if pd.isna(v):
                continue
            out.append(InputIndicatorDataPoint(name=name, value=float(v)))
        return out

    @property
    def n_rows(self) -> int:
        if not self._series:
            return 0
        return int(self._series[0][1].shape[0])
