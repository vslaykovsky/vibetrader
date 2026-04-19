from datetime import date

import pandas as pd
import pytest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from application.queries.historical_bars import HistoricalBarsQuery, scale_to_timeframe


def _tf_key(tf: TimeFrame) -> tuple:
    return (tf.amount, tf.unit)


def test_scale_to_timeframe_maps_common_scales():
    assert _tf_key(scale_to_timeframe("1m")) == _tf_key(TimeFrame.Minute)
    assert _tf_key(scale_to_timeframe("15M")) == (15, TimeFrameUnit.Minute)
    assert _tf_key(scale_to_timeframe("1h")) == _tf_key(TimeFrame.Hour)
    assert _tf_key(scale_to_timeframe("4h")) == (4, TimeFrameUnit.Hour)
    assert _tf_key(scale_to_timeframe("1d")) == _tf_key(TimeFrame.Day)
    assert _tf_key(scale_to_timeframe("1w")) == _tf_key(TimeFrame.Week)


def test_scale_to_timeframe_rejects_unknown():
    with pytest.raises(ValueError, match="Unsupported scale"):
        scale_to_timeframe("2h")


def test_historical_bars_query_fetch_delegates(monkeypatch):
    expected = pd.DataFrame(
        {"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [10.0]},
        index=pd.to_datetime(["2024-01-02"]),
    )
    calls: list[dict] = []

    def fake_fetch_stock_bars(**kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(
        "application.queries.historical_bars.utils.fetch_stock_bars", fake_fetch_stock_bars
    )

    q = HistoricalBarsQuery()
    out = q.fetch(
        "SPY",
        "1d",
        date(2024, 1, 1),
        date(2024, 1, 31),
        padding_days=5,
        provider="alpaca",
    )

    assert out.equals(expected)
    assert len(calls) == 1
    c0 = calls[0]
    assert c0["ticker"] == "SPY"
    assert c0["start_test_date"] == "2024-01-01"
    assert c0["end_test_date"] == "2024-01-31"
    assert c0["history_padding_days"] == 5
    assert c0["provider"] == "alpaca"
    assert _tf_key(c0["timeframe"]) == _tf_key(TimeFrame.Day)


def test_historical_bars_query_cache_reuses_fetch(monkeypatch):
    expected = pd.DataFrame(
        {"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [10.0]},
        index=pd.to_datetime(["2024-01-02"]),
    )
    calls: list[int] = []

    def fake_fetch_stock_bars(**kwargs):
        calls.append(1)
        return expected

    monkeypatch.setattr(
        "application.queries.historical_bars.utils.fetch_stock_bars", fake_fetch_stock_bars
    )

    q = HistoricalBarsQuery(cache_ttl_seconds=600.0)
    a = q.fetch("SPY", "1d", date(2024, 1, 1), date(2024, 1, 31), padding_days=5, provider="alpaca")
    b = q.fetch("SPY", "1d", date(2024, 1, 1), date(2024, 1, 31), padding_days=5, provider="alpaca")
    assert a.equals(expected)
    assert b.equals(expected)
    assert len(calls) == 1
